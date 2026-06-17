"""nanny.icinga — peeled from nanny.core (Phase 2)."""

import os
import re
import sys
import ssl
import json
import time
import socket
import threading
import requests
from concurrent.futures import ThreadPoolExecutor
from nanny.config import *
from nanny.util import *
from nanny.log import *

__all__ = ['_ICINGA_SESSION', '_icinga_http', '_icinga_get', '_ICINGA_ATTRS', '_icinga_entry', '_icinga_downtimes', 'icinga_inventory', '_ICINGA_HOST_STATE', '_ICINGA_SVC_STATE', '_icinga_lookup', '_tool_icinga_state', '_format_icinga_state']


_ICINGA_SESSION = None


def _icinga_http(verify):
    """HTTP caller for the Icinga API. Normally the plain `requests` module; if
    ICINGA_LEGACY_TLS is set, a Session whose TLS floor is lowered (min TLSv1,
    cipher SECLEVEL 0) so a modern OpenSSL can still talk to ancient Icinga (≤2.x,
    whose cert/cipher trips OpenSSL 3's default security level). Gated off by default
    so modern installs keep a strong floor."""
    global _ICINGA_SESSION
    if not _env_flag("ICINGA_LEGACY_TLS"):
        return requests
    if _ICINGA_SESSION is None:
        import ssl
        from requests.adapters import HTTPAdapter

        class _LegacyTLSAdapter(HTTPAdapter):
            def init_poolmanager(self, *a, **k):
                ctx = ssl.create_default_context()
                ctx.minimum_version = ssl.TLSVersion.TLSv1
                try:
                    ctx.set_ciphers("DEFAULT@SECLEVEL=0")
                except ssl.SSLError:
                    pass
                if verify is False:
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                elif isinstance(verify, str):
                    ctx.load_verify_locations(verify)
                k["ssl_context"] = ctx
                return super().init_poolmanager(*a, **k)

        s = requests.Session()
        s.mount("https://", _LegacyTLSAdapter())
        _ICINGA_SESSION = s
    return _ICINGA_SESSION


def _icinga_get(path: str, body: dict = None) -> list:
    """Call the Icinga2 REST API and return its `results` list. Filtered reads use
    POST + X-HTTP-Method-Override (Icinga's way of passing a query body on a GET).
    TLS verifies against ICINGA_API_CA if given, or is skipped with ICINGA_INSECURE
    (Icinga's API cert is self-signed by default)."""
    base = _env_dir("ICINGA_API_URL", "").rstrip("/")
    if not base:
        raise RuntimeError("ICINGA_API_URL is not set")
    ca = _env_dir("ICINGA_API_CA", "")
    verify = ca if ca else not _env_flag("ICINGA_INSECURE")
    if verify is False:
        try:
            import urllib3
            urllib3.disable_warnings()
        except Exception:
            pass
    auth = (os.environ.get("ICINGA_API_USER", ""), os.environ.get("ICINGA_API_PASSWORD", ""))
    headers = {"Accept": "application/json"}
    url = f"{base}/v1/{path.lstrip('/')}"
    http = _icinga_http(verify)
    # Always pass `verify`: with the legacy-TLS session, omitting it lets requests
    # default verify=True back on and re-enable cert checks, overriding the custom
    # context's CERT_NONE. verify=False → requests sets the pool to CERT_NONE.
    kw = {"headers": headers, "auth": auth, "timeout": (5, 20), "verify": verify}
    if body is not None:
        headers["X-HTTP-Method-Override"] = "GET"
    # Retry transient transport failures (connect/read timeouts, dropped
    # connections) and retryable server states. Auth/permission errors (401/403)
    # are NOT retried — raise_for_status surfaces them immediately. The count is
    # tunable via ICINGA_RETRIES; the default absorbs flaky network paths.
    try:
        retries = max(0, int(_env_dir("ICINGA_RETRIES", "4")))
    except ValueError:
        retries = 4
    last = None
    for attempt in range(retries + 1):
        try:
            r = http.get(url, **kw) if body is None else http.post(url, json=body, **kw)
            if r.status_code in (429, 502, 503, 504) and attempt < retries:
                last = RuntimeError(f"HTTP {r.status_code}")
            else:
                r.raise_for_status()
                return r.json().get("results", [])
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            last = e
            if attempt >= retries:
                raise
        time.sleep(min(0.5 * (2 ** attempt), 8))
    raise last


_ICINGA_ATTRS = ["display_name", "state", "state_type", "last_check_result",
                 "last_state_change", "last_check", "check_command", "check_attempt",
                 "max_check_attempts", "acknowledgement", "notes", "notes_url"]


def _icinga_entry(name, st, a, dt) -> dict:
    """Build one enriched fleet entry from an object's attrs (`a`); `dt` is the
    downtime record for it, if any."""
    lcr = a.get("last_check_result") or {}
    entry = {
        "service": name,
        "state": st,
        "up": st == 0,
        "state_type": "hard" if int(a.get("state_type", 1) or 0) == 1 else "soft",
        "output": _trunc(lcr.get("output", ""), 220),
        "perf": _trunc(_fmt_perf(lcr.get("performance_data")), 200),
        "age": _icinga_age(a.get("last_state_change")),
        "last_check": _icinga_age(a.get("last_check") or lcr.get("execution_end")),
        "attempt": f"{int(a.get('check_attempt', 1) or 1)}/{int(a.get('max_check_attempts', 1) or 1)}",
        "command": a.get("check_command", ""),
        "acked": bool(int(a.get("acknowledgement", 0) or 0)),
        "downtime": bool(dt),                            # derived from /objects/downtimes
        "notes": _trunc(a.get("notes", ""), 280),
        "notes_url": a.get("notes_url", "") or "",
    }
    if dt:
        entry["downtime_info"] = dt
    return entry


def _icinga_downtimes() -> dict:
    """Map (host_name, service_name) -> {author, comment, ends_in} for active
    downtimes, so the fleet view can show who scheduled them and until when."""
    dmap = {}
    try:
        rows = _icinga_get("objects/downtimes",
                           {"attrs": ["host_name", "service_name", "author",
                                      "comment", "end_time"]})
    except Exception:
        return dmap
    now = time.time()
    for d in rows:
        a = d.get("attrs", {})
        key = (a.get("host_name", ""), a.get("service_name", "") or "")
        try:
            ends_in = int(float(a.get("end_time", 0) or 0) - now)
        except (TypeError, ValueError):
            ends_in = None
        dmap[key] = {"author": a.get("author", ""),
                     "comment": _trunc(a.get("comment", ""), 180),
                     "ends_in": ends_in}
    return dmap


def icinga_inventory() -> dict:
    """Fleet inventory from Icinga, keyed by host address: one enriched entry per
    host (its own UP/DOWN) plus one per service (OK/WARN/CRIT/UNKNOWN). Each entry
    carries the plugin output, notes, downtime/ack, age and freshness — see
    _icinga_entry. `up` is true only when Icinga reports the check fully healthy."""
    inv = {}
    dmap = _icinga_downtimes()
    for h in _icinga_get("objects/hosts", {"attrs": ["address"] + _ICINGA_ATTRS}):
        a = h.get("attrs", {})
        hname = h.get("name", "")
        addr = a.get("address") or hname or a.get("display_name") or "?"
        st = int(a.get("state", 1) or 0)               # host: 0=UP, 1=DOWN, 2=UNREACHABLE
        e = _icinga_entry("host", st, a, dmap.get((hname, "")))
        e["host_display"] = a.get("display_name") or hname
        inv.setdefault(addr, []).append(e)
    for s in _icinga_get("objects/services",
                         {"attrs": _ICINGA_ATTRS + ["vars"],
                          "joins": ["host.name", "host.address"]}):
        a = s.get("attrs", {})
        host = s.get("joins", {}).get("host", {})
        hname = host.get("name", "")
        addr = host.get("address") or hname or "?"
        short = s.get("name", "").split("!")[-1]
        name = a.get("display_name") or short
        st = int(a.get("state", 3) or 0)               # service: 0=OK 1=WARN 2=CRIT 3=UNKNOWN
        entry = _icinga_entry(name, st, a, dmap.get((hname, short)))
        port = (a.get("vars") or {}).get("port")        # services may carry a port var
        if port is not None:
            try:
                entry["port"] = int(port)
            except (TypeError, ValueError):
                pass
        inv.setdefault(addr, []).append(entry)
    return inv


_ICINGA_HOST_STATE = {0: "UP", 1: "DOWN", 2: "UNREACHABLE"}


_ICINGA_SVC_STATE = {0: "OK", 1: "WARNING", 2: "CRITICAL", 3: "UNKNOWN"}


def _icinga_lookup(host: str, service: str = None) -> dict:
    """Live Icinga state for a single host + its services (read-only). Used by the
    triage agent and the triage view. Matches on host name OR address via a
    parameterised filter (filter_vars — no string interpolation, so an attacker
    can't inject Icinga filter expressions through an alert's host field). Returns
    {host, services, notes_url, notes}; empty/partial on any failure."""
    out = {"host": None, "services": [], "notes_url": "", "notes": ""}
    if not _icinga_configured() or not host:
        return out
    flt = "host.name==h || host.address==h"
    fv = {"h": host}
    try:
        hosts = _icinga_get("objects/hosts",
                            {"attrs": ["address"] + _ICINGA_ATTRS,
                             "filter": flt, "filter_vars": fv})
    except Exception:
        hosts = []
    if hosts:
        a = hosts[0].get("attrs", {})
        e = _icinga_entry("host", int(a.get("state", 1) or 0), a, None)
        e["host_display"] = a.get("display_name") or hosts[0].get("name", "")
        out["host"] = e
    try:
        svcs = _icinga_get("objects/services",
                           {"attrs": _ICINGA_ATTRS,
                            "joins": ["host.name", "host.address"],
                            "filter": flt, "filter_vars": fv})
    except Exception:
        svcs = []
    for s in svcs:
        a = s.get("attrs", {})
        short = s.get("name", "").split("!")[-1]
        out["services"].append(
            _icinga_entry(a.get("display_name") or short, int(a.get("state", 3) or 0), a, None))
    target = None
    if service:
        target = next((e for e in out["services"]
                       if service.lower() in (e["service"] or "").lower()), None)
    src = target or out["host"] or {}
    out["notes_url"] = src.get("notes_url", "") or (out["host"] or {}).get("notes_url", "")
    out["notes"] = src.get("notes", "") or (out["host"] or {}).get("notes", "")
    return out


def _tool_icinga_state(host: str, service: str = None) -> str:
    """get_icinga_state tool entrypoint: look up + format. (handle_firing reuses
    _format_icinga_state directly so it doesn't query Icinga twice.)"""
    host = (host or "").strip()
    if not host:
        return "ERROR: host is required"
    return _format_icinga_state(_icinga_lookup(host, (service or "").strip() or None), host)


def _format_icinga_state(d: dict, host: str) -> str:
    """Format an _icinga_lookup result as a compact text block for the triage agent."""
    if not d.get("host") and not d.get("services"):
        return (f"No Icinga object found for host {host!r} "
                "(Icinga may be unconfigured/unreachable, or the name didn't match).")
    lines, h = [], d.get("host")
    if h:
        lines.append(f"HOST {h.get('host_display') or host}: "
                     f"{_ICINGA_HOST_STATE.get(h['state'], '?')} — {_trunc(h.get('output', ''), 160)}")
        if h.get("notes_url"):
            lines.append(f"  host runbook: {h['notes_url']}")
    svcs = d.get("services", [])
    problems = [e for e in svcs if e["state"] != 0]
    lines.append(f"SERVICES on this host: {len(svcs)} total, {len(problems)} not OK"
                 + (" — likely a host-wide problem" if len(problems) > 1 else ""))
    for e in sorted(svcs, key=lambda e: -e["state"])[:25]:
        flag = " [DOWNTIME]" if e.get("downtime") else (" [ACK]" if e.get("acked") else "")
        lines.append(f"  [{_ICINGA_SVC_STATE.get(e['state'], '?')}] {e['service']}{flag}: "
                     f"{_trunc(e.get('output', ''), 120)}")
    if d.get("notes_url"):
        lines.append(f"RUNBOOK (notes_url): {d['notes_url']}")
    return "\n".join(lines)
