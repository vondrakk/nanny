"""nanny durable state, behind one interface.

Two backends implement the same `Store` API:

  * `MemoryStore`   — in-process dicts/deque (the original single-node behaviour);
                      used when DATABASE_URL is unset (local/dev).
  * `PostgresStore` — CloudNativePG-backed; the source of truth for stateless,
                      multi-replica deployments (see ../ARCHITECTURE.md).

`make_store()` picks one from the environment. Everything the web/brain needs to
read or mutate shared state goes through this object, so the rest of nanny is
backend-agnostic. Time is UTC; callers pass plain epoch seconds where noted.
"""

import os
import json
import time
import threading
from datetime import datetime, timezone


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── interface ─────────────────────────────────────────────────────────────────
class Store:
    """Abstract durable-state interface. See MemoryStore for reference semantics."""

    # lifecycle
    def init_schema(self): ...
    def healthy(self) -> bool: return True

    # sessions
    def session_create(self, token, name): ...
    def session_get(self, token, idle_ttl, abs_ttl): ...
    def session_touch(self, token, idle_ttl, abs_ttl): ...
    def session_delete(self, token): ...
    def sessions_online(self, online_ttl): ...

    # incidents
    def incident_create(self, rec) -> bool: ...        # True if newly created
    def incident_get(self, fp): ...
    def incident_update(self, fp, **fields): ...
    def incidents_firing(self): ...
    def incident_resolve(self, fp): ...                # returns prior record or None
    def claim_due_pages(self): ...                     # reconciler: atomically claim+return
    def incident_claim(self, fp, op_id, name): ...     # (code, body)
    def incident_ack(self, fp, op_id, name, idem): ...
    def incident_release(self, fp, op_id): ...
    def busy_count(self) -> int: ...

    # history (adaptive hold)
    def history_record(self, sig, self_resolved, duration): ...
    def history_samples(self, sig): ...

    # resolved-incident log
    def log_incident(self, rec): ...
    def read_incidents(self, window_secs): ...

    # activity / audit feed
    def activity_add(self, kind, text, extra=None) -> dict: ...
    def activity_since(self, since, limit): ...

    # jobs
    def job_add(self, rec): ...
    def job_update(self, job_id, **fields): ...
    def jobs_snapshot(self, limit): ...

    def job_running(self, kind) -> bool:
        return any(j.get("kind") == kind and j.get("status") == "running"
                   for j in self.jobs_snapshot(50))


def make_store():
    """PostgresStore when DATABASE_URL is set, else MemoryStore."""
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if url:
        return PostgresStore(url)
    return MemoryStore(data_dir=os.environ.get("NANNY_DATA_DIR", "data"))


# ── in-memory backend (single node / dev) ──────────────────────────────────────
class MemoryStore(Store):
    def __init__(self, data_dir="data"):
        self._dir = data_dir
        self._lock = threading.RLock()
        self._sessions = {}        # token -> {name, created, last_seen}
        self._incidents = {}       # fp -> record
        self._jobs = {}
        self._log = []             # activity log
        self._seq = 0

    # files (history + incident_log persist like the original)
    def _path(self, name):
        return os.path.join(self._dir, name)

    def _load(self, name, default):
        try:
            return json.load(open(self._path(name)))
        except (OSError, ValueError):
            return default

    def _save(self, name, obj):
        p = self._path(name)
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        with open(p, "w") as fh:
            json.dump(obj, fh)

    def init_schema(self):
        pass

    # sessions
    def _alive(self, s, now, idle_ttl, abs_ttl):
        return now - s["last_seen"] < idle_ttl and now - s["created"] < abs_ttl

    def session_create(self, token, name):
        now = time.time()
        with self._lock:
            self._sessions[token] = {"name": name or "operator", "created": now, "last_seen": now}

    def session_get(self, token, idle_ttl, abs_ttl):
        if not token:
            return None
        now = time.time()
        with self._lock:
            s = self._sessions.get(token)
            if not s:
                return None
            if not self._alive(s, now, idle_ttl, abs_ttl):
                self._sessions.pop(token, None)
                return None
            return dict(s)

    def session_touch(self, token, idle_ttl, abs_ttl):
        now = time.time()
        with self._lock:
            s = self._sessions.get(token)
            if s and self._alive(s, now, idle_ttl, abs_ttl):
                s["last_seen"] = now

    def session_delete(self, token):
        with self._lock:
            self._sessions.pop(token, None)

    def sessions_online(self, online_ttl):
        now = time.time()
        with self._lock:
            return sorted(({"name": s["name"], "idle": int(now - s["last_seen"])}
                           for s in self._sessions.values()
                           if now - s["last_seen"] < online_ttl),
                          key=lambda o: o["name"])

    # incidents
    def incident_create(self, rec):
        fp = rec["fingerprint"]
        with self._lock:
            if fp in self._incidents and self._incidents[fp].get("status") == "firing":
                return False
            self._incidents[fp] = dict(rec, status="firing")
            return True

    def incident_get(self, fp):
        with self._lock:
            r = self._incidents.get(fp)
            return dict(r) if r else None

    def incident_update(self, fp, **fields):
        with self._lock:
            if fp in self._incidents:
                self._incidents[fp].update(fields)

    def incidents_firing(self):
        with self._lock:
            return [dict(r) for r in self._incidents.values() if r.get("status") == "firing"]

    def incident_resolve(self, fp):
        with self._lock:
            r = self._incidents.pop(fp, None)
            return dict(r) if r else None

    def claim_due_pages(self):
        now = time.time()
        out = []
        with self._lock:
            for r in self._incidents.values():
                if (r.get("status") == "firing" and not r.get("paged")
                        and not r.get("acked") and not r.get("owner")
                        and r.get("page_at") is not None and r["page_at"] <= now):
                    r["paged"] = True
                    r["phase"] = "paged"
                    out.append(dict(r))
        return out

    def incident_claim(self, fp, op_id, name):
        with self._lock:
            r = self._incidents.get(fp)
            if not r:
                return 404, {"error": "no such incident"}
            if r.get("owner") and r["owner"] != op_id:
                return 409, {"error": "already claimed", "owner": r.get("owner_name")}
            r["owner"], r["owner_name"] = op_id, name
            return 200, {"owner": name}

    def incident_ack(self, fp, op_id, name, idem):
        # Returns (200, result). On the genuine first ack the result carries
        # first=True so the caller fires the one-time PagerDuty ack; idempotency
        # replays return the cached result WITHOUT first, so side-effects run once.
        with self._lock:
            r = self._incidents.get(fp)
            if not r:
                return 404, {"error": "no such incident"}
            cache = r.setdefault("ack_idem", {})
            if idem and idem in cache:
                return 200, dict(cache[idem])
            first = not r.get("acked")
            r["acked"] = True
            if not r.get("owner"):
                r["owner"], r["owner_name"] = op_id, name
            res = {"acked": True, "owner": r.get("owner_name")}
            if idem:
                cache[idem] = dict(res)               # cached result excludes `first`
            return 200, dict(res, first=first)

    def incident_release(self, fp, op_id):
        with self._lock:
            r = self._incidents.get(fp)
            if not r:
                return 404, {"error": "no such incident"}
            if r.get("owner") == op_id:
                r["owner"] = r["owner_name"] = None
            return 200, {"released": True}

    def busy_count(self):
        with self._lock:
            n = sum(1 for r in self._incidents.values()
                    if r.get("status") == "firing" and r.get("phase") in ("triaging", "holding"))
            n += sum(1 for j in self._jobs.values() if j.get("status") == "running")
            return n

    # history
    def history_record(self, sig, self_resolved, duration):
        with self._lock:
            hist = self._load("history.json", {})
            s = hist.setdefault(sig, {"samples": []})
            s["samples"].append({"self_resolved": bool(self_resolved), "duration": round(duration)})
            s["samples"] = s["samples"][-50:]
            self._save("history.json", hist)

    def history_samples(self, sig):
        return (self._load("history.json", {}).get(sig) or {}).get("samples", [])

    # incident log
    def log_incident(self, rec):
        p = self._path("incidents.jsonl")
        with self._lock:
            os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
            with open(p, "a") as fh:
                fh.write(json.dumps(rec) + "\n")

    def read_incidents(self, window_secs):
        cutoff = time.time() - window_secs
        out = []
        try:
            for ln in open(self._path("incidents.jsonl")):
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                ts = r.get("ended", "")
                try:
                    when = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
                except ValueError:
                    when = cutoff
                if when >= cutoff:
                    out.append(r)
        except OSError:
            pass
        return out

    # activity
    def activity_add(self, kind, text, extra=None):
        with self._lock:
            self._seq += 1
            rec = {"id": self._seq, "ts": _now_iso(), "kind": kind, "text": text}
            if extra:
                rec.update(extra)
            self._log.append(rec)
            self._log = self._log[-2000:]
            return dict(rec)

    def activity_since(self, since, limit):
        with self._lock:
            return [dict(r) for r in self._log if r["id"] > since][-limit:]

    # jobs
    def job_add(self, rec):
        with self._lock:
            self._jobs[rec["id"]] = dict(rec)

    def job_update(self, job_id, **fields):
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id].update(fields)

    def jobs_snapshot(self, limit):
        with self._lock:
            return sorted((dict(j) for j in self._jobs.values()),
                          key=lambda j: j.get("started", ""), reverse=True)[:limit]


# ── postgres backend (stateless / CNPG) ─────────────────────────────────────────
class PostgresStore(Store):
    def __init__(self, url):
        self._url = url
        import psycopg                                  # imported lazily — optional dep
        from psycopg.rows import dict_row
        self._psycopg = psycopg
        self._dict_row = dict_row

    def _conn(self):
        # Connection per operation: simple and safe under threads. (A psycopg_pool
        # is the natural optimisation once this is the hot path.)
        return self._psycopg.connect(self._url, autocommit=True, row_factory=self._dict_row)

    def init_schema(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sql = open(os.path.join(here, "db", "schema.sql")).read()
        with self._conn() as c:
            c.execute(sql)

    def healthy(self):
        try:
            with self._conn() as c:
                c.execute("SELECT 1")
            return True
        except Exception:
            return False

    # sessions
    def session_create(self, token, name):
        with self._conn() as c:
            c.execute("INSERT INTO sessions(token,name) VALUES(%s,%s) "
                      "ON CONFLICT(token) DO NOTHING", (token, name or "operator"))

    def session_get(self, token, idle_ttl, abs_ttl):
        if not token:
            return None
        with self._conn() as c:
            r = c.execute(
                "SELECT name, extract(epoch from created) AS created, "
                "extract(epoch from last_seen) AS last_seen FROM sessions "
                "WHERE token=%s AND last_seen > now() - make_interval(secs=>%s) "
                "AND created > now() - make_interval(secs=>%s)",
                (token, idle_ttl, abs_ttl)).fetchone()
            if not r:
                c.execute("DELETE FROM sessions WHERE token=%s AND "
                          "(last_seen <= now() - make_interval(secs=>%s) OR "
                          " created <= now() - make_interval(secs=>%s))",
                          (token, idle_ttl, abs_ttl))
            return r

    def session_touch(self, token, idle_ttl, abs_ttl):
        with self._conn() as c:
            c.execute("UPDATE sessions SET last_seen=now() WHERE token=%s "
                      "AND last_seen > now() - make_interval(secs=>%s) "
                      "AND created > now() - make_interval(secs=>%s)",
                      (token, idle_ttl, abs_ttl))

    def session_delete(self, token):
        with self._conn() as c:
            c.execute("DELETE FROM sessions WHERE token=%s", (token,))

    def sessions_online(self, online_ttl):
        with self._conn() as c:
            rows = c.execute(
                "SELECT name, extract(epoch from now()-last_seen)::int AS idle "
                "FROM sessions WHERE last_seen > now() - make_interval(secs=>%s) "
                "ORDER BY name", (online_ttl,)).fetchall()
            return rows

    # incidents
    _ICOLS = ("summary sig severity instance service phase hold_secs page_at paged "
              "owner owner_name acked ticket runbook notes analysis audit descr").split()

    def incident_create(self, rec):
        page_at = rec.get("page_at")
        with self._conn() as c:
            r = c.execute(
                "INSERT INTO incidents(fingerprint,status,summary,sig,severity,instance,"
                "service,phase,hold_secs,page_at,started) "
                "VALUES(%s,'firing',%s,%s,%s,%s,%s,%s,%s,to_timestamp(%s),now()) "
                "ON CONFLICT(fingerprint) DO NOTHING RETURNING fingerprint",
                (rec["fingerprint"], rec.get("summary"), rec.get("sig"), rec.get("severity"),
                 rec.get("instance"), rec.get("service"), rec.get("phase", "triaging"),
                 rec.get("hold_secs", 0), page_at)).fetchone()
            return r is not None

    def _row_to_inc(self, r):
        if not r:
            return None
        r = dict(r)
        for k in ("page_at", "started", "updated"):
            if r.get(k) is not None and hasattr(r[k], "timestamp"):
                r[k] = r[k].timestamp()
        return r

    def incident_get(self, fp):
        with self._conn() as c:
            return self._row_to_inc(c.execute(
                "SELECT *, extract(epoch from now()-started)::int AS age "
                "FROM incidents WHERE fingerprint=%s", (fp,)).fetchone())

    def incident_update(self, fp, **fields):
        if not fields:
            return
        cols, vals = [], []
        for k, v in fields.items():
            col = "descr" if k == "desc" else k
            if k == "page_at" and v is not None:
                cols.append(f"{col}=to_timestamp(%s)")
            elif k == "audit":
                cols.append(f"{col}=%s::jsonb"); v = json.dumps(v)
            else:
                cols.append(f"{col}=%s")
            vals.append(v)
        vals.append(fp)
        with self._conn() as c:
            c.execute(f"UPDATE incidents SET {','.join(cols)}, updated=now() "
                      f"WHERE fingerprint=%s", vals)

    def incidents_firing(self):
        with self._conn() as c:
            return [self._row_to_inc(r) for r in c.execute(
                "SELECT *, extract(epoch from now()-started)::int AS age "
                "FROM incidents WHERE status='firing'").fetchall()]

    def incident_resolve(self, fp):
        with self._conn() as c:
            return self._row_to_inc(c.execute(
                "UPDATE incidents SET status='resolved', updated=now() "
                "WHERE fingerprint=%s AND status='firing' RETURNING *", (fp,)).fetchone())

    def claim_due_pages(self):
        with self._conn() as c:
            rows = c.execute(
                "UPDATE incidents SET paged=true, phase='paged', updated=now() "
                "WHERE fingerprint IN ("
                "  SELECT fingerprint FROM incidents "
                "   WHERE status='firing' AND paged=false AND acked=false AND owner IS NULL "
                "     AND page_at IS NOT NULL AND page_at <= now() "
                "   FOR UPDATE SKIP LOCKED LIMIT 25) RETURNING *").fetchall()
            return [self._row_to_inc(r) for r in rows]

    def incident_claim(self, fp, op_id, name):
        with self._conn() as c:
            r = c.execute("UPDATE incidents SET owner=%s, owner_name=%s, updated=now() "
                          "WHERE fingerprint=%s AND (owner IS NULL OR owner=%s) "
                          "RETURNING owner_name", (op_id, name, fp, op_id)).fetchone()
            if r:
                return 200, {"owner": r["owner_name"]}
            cur = c.execute("SELECT owner_name FROM incidents WHERE fingerprint=%s",
                            (fp,)).fetchone()
            if not cur:
                return 404, {"error": "no such incident"}
            return 409, {"error": "already claimed", "owner": cur["owner_name"]}

    def incident_ack(self, fp, op_id, name, idem):
        with self._conn() as c:
            cur = c.execute("SELECT ack_idem, owner, acked FROM incidents WHERE fingerprint=%s",
                            (fp,)).fetchone()
            if not cur:
                return 404, {"error": "no such incident"}
            if idem and idem in (cur["ack_idem"] or {}):
                return 200, dict(cur["ack_idem"][idem])
            first = not cur["acked"]
            r = c.execute(
                "UPDATE incidents SET acked=true, "
                "owner=COALESCE(owner,%s), owner_name=COALESCE(owner_name,%s), updated=now() "
                "WHERE fingerprint=%s RETURNING owner_name", (op_id, name, fp)).fetchone()
            res = {"acked": True, "owner": r["owner_name"]}
            if idem:
                c.execute("UPDATE incidents SET ack_idem = ack_idem || %s::jsonb "
                          "WHERE fingerprint=%s", (json.dumps({idem: res}), fp))
            return 200, dict(res, first=first)

    def incident_release(self, fp, op_id):
        with self._conn() as c:
            r = c.execute("UPDATE incidents SET owner=NULL, owner_name=NULL, updated=now() "
                          "WHERE fingerprint=%s AND owner=%s RETURNING fingerprint",
                          (fp, op_id)).fetchone()
            if not c.execute("SELECT 1 FROM incidents WHERE fingerprint=%s", (fp,)).fetchone():
                return 404, {"error": "no such incident"}
            return 200, {"released": True}

    def busy_count(self):
        with self._conn() as c:
            n = c.execute("SELECT count(*) AS n FROM incidents "
                          "WHERE status='firing' AND phase IN ('triaging','holding')"
                          ).fetchone()["n"]
            n += c.execute("SELECT count(*) AS n FROM jobs WHERE status='running'"
                           ).fetchone()["n"]
            return int(n)

    # history
    def history_record(self, sig, self_resolved, duration):
        sample = {"self_resolved": bool(self_resolved), "duration": round(duration)}
        with self._conn() as c:
            c.execute(
                "INSERT INTO history(sig,samples) VALUES(%s, %s::jsonb) "
                "ON CONFLICT(sig) DO UPDATE SET samples = "
                "(SELECT jsonb_agg(s) FROM (SELECT s FROM jsonb_array_elements("
                "history.samples || %s::jsonb) s "
                "OFFSET GREATEST(0, jsonb_array_length(history.samples||%s::jsonb)-50)) t)",
                (sig, json.dumps([sample]), json.dumps([sample]), json.dumps([sample])))

    def history_samples(self, sig):
        with self._conn() as c:
            r = c.execute("SELECT samples FROM history WHERE sig=%s", (sig,)).fetchone()
            return (r["samples"] if r else []) or []

    # incident log
    def log_incident(self, rec):
        with self._conn() as c:
            c.execute("INSERT INTO incident_log"
                      "(sig,alertname,service,instance,ticket,paged,self_resolved,closed,duration)"
                      " VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                      (rec.get("sig"), rec.get("alertname"), rec.get("service"),
                       rec.get("instance"), rec.get("ticket"), rec.get("paged"),
                       rec.get("self_resolved"), rec.get("closed"), rec.get("duration")))

    def read_incidents(self, window_secs):
        with self._conn() as c:
            return [dict(r, ended=r["ended"].isoformat()) for r in c.execute(
                "SELECT *, ended FROM incident_log "
                "WHERE ended > now() - make_interval(secs=>%s) ORDER BY ended",
                (window_secs,)).fetchall()]

    # activity
    def activity_add(self, kind, text, extra=None):
        with self._conn() as c:
            r = c.execute("INSERT INTO activity_log(kind,text,extra) "
                          "VALUES(%s,%s,%s::jsonb) RETURNING id, extract(epoch from ts) AS ts",
                          (kind, text, json.dumps(extra or {}))).fetchone()
            rec = {"id": r["id"], "ts": _now_iso(), "kind": kind, "text": text}
            if extra:
                rec.update(extra)
            return rec

    def activity_since(self, since, limit):
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, to_char(ts,'YYYY-MM-DD\"T\"HH24:MI:SS') AS ts, kind, text, extra "
                "FROM activity_log WHERE id > %s ORDER BY id LIMIT %s", (since, limit)).fetchall()
            out = []
            for r in rows:
                rec = {"id": r["id"], "ts": r["ts"], "kind": r["kind"], "text": r["text"]}
                rec.update(r.get("extra") or {})
                out.append(rec)
            return out

    # jobs
    def job_add(self, rec):
        with self._conn() as c:
            c.execute("INSERT INTO jobs(id,kind,status,started,summary,error) "
                      "VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING",
                      (rec["id"], rec.get("kind"), rec.get("status"),
                       rec.get("started"), rec.get("summary"), rec.get("error")))

    def job_update(self, job_id, **fields):
        if not fields:
            return
        cols = ", ".join(f"{k}=%s" for k in fields)
        with self._conn() as c:
            c.execute(f"UPDATE jobs SET {cols} WHERE id=%s", list(fields.values()) + [job_id])

    def jobs_snapshot(self, limit):
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT id,kind,status,started,finished,summary,error "
                "FROM jobs ORDER BY started DESC LIMIT %s", (limit,)).fetchall()]
