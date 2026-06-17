"""Phase-3 stateless lifecycle tests: incident state through the Store + the paging
reconciler that replaced the in-memory hold thread.

Runs offline with no third-party deps: `requests`/`anthropic` are stubbed in
sys.modules before importing nanny.core, the network-touching integration calls are
monkeypatched to recorders, and the incident hold is driven by forcing `page_at`
into the past so no real time has to elapse. Backed by MemoryStore (DATABASE_URL
unset). Run: `python tests/test_reconciler.py` (also importable under pytest).
"""

import os
import sys
import time
import types
import tempfile

# ── isolate state + stub heavy deps BEFORE importing nanny.core ────────────────
_TMP = tempfile.mkdtemp(prefix="nanny-test-")
os.environ["NANNY_DATA_DIR"] = _TMP
os.environ["NANNY_DRY_RUN"] = "true"          # _simulated_triage, no LLM / no Icinga
os.environ.pop("DATABASE_URL", None)          # force MemoryStore


def _stub(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


_stub("requests")
_stub("anthropic", Anthropic=object)
_stub("slack_sdk", WebClient=object)
_sb = _stub("slack_bolt", App=object)
_ad = _stub("slack_bolt.adapter")
_sm = _stub("slack_bolt.adapter.socket_mode", SocketModeHandler=object)
_sb.adapter = _ad
_ad.socket_mode = _sm

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nanny import core  # noqa: E402

# ── neutralise every side-effecting integration call; record the pages ─────────
PD = []        # (action, fp) tuples from pd_event
NOTIFS = []    # (kind, text)

core.create_jira_issue = lambda *a, **k: ("TEST-1", "http://jira.example/TEST-1")
core.add_jira_comment = lambda *a, **k: None
core._jira_close = lambda *a, **k: None
core.post_incident = lambda *a, **k: None
core.cards_enabled = lambda: False
core._icinga_lookup = lambda *a, **k: {}
core.pd_event = lambda action, fp, *a, **k: PD.append((action, fp))
core.notify = lambda kind, text, *a, **k: NOTIFS.append((kind, text))

S = core.STORE


def _alert(fp, hold=None):
    a = {"status": "firing", "fingerprint": fp,
         "labels": {"alertname": "DiskFull", "service": "db", "instance": "h1",
                    "severity": "critical"},
         "annotations": {"summary": f"disk full on {fp}", "description": "95%"},
         "startsAt": "2026-06-10T00:00:00Z"}
    if hold is not None:
        a["_hold"] = hold
    return a


def _force_due(fp):
    """Pretend the hold elapsed — push page_at into the past."""
    S.incident_update(fp, page_at=time.time() - 1)


def test_firing_then_reconciler_pages_once():
    fp = "fp/page"
    core.handle_firing(_alert(fp, hold=120))
    inc = S.incident_get(fp)
    assert inc and inc["status"] == "firing", "incident should be firing"
    assert inc["phase"] == "holding" and not inc["paged"], "should hold, not page inline"
    assert inc["page_at"] is not None, "page_at must be scheduled"
    assert inc.get("ticket") == "TEST-1", "ticket should be recorded"

    _force_due(fp)
    paged = core._reconcile_once()
    assert paged == 1, f"reconciler should page 1, got {paged}"
    assert ("trigger", fp) in PD, "PagerDuty trigger should fire"
    inc = S.incident_get(fp)
    assert inc["paged"] and inc["phase"] == "paged", "incident should be marked paged"

    # exactly-once: a second pass pages nothing
    assert core._reconcile_once() == 0, "already-paged incident must not re-page"
    print("PASS firing → reconciler pages exactly once")


def test_claim_suppresses_page():
    fp = "fp/claim"
    op = core._op_login("alice")
    core.handle_firing(_alert(fp, hold=120))
    code, _ = core._claim(fp, op)
    assert code == 200, "claim should succeed"
    inc = S.incident_get(fp)
    assert inc["owner_name"] == "alice" and inc["phase"] == "claimed"

    _force_due(fp)
    before = len(PD)
    assert core._reconcile_once() == 0, "owned incident must not page"
    assert len(PD) == before, "no PagerDuty trigger for a claimed incident"
    print("PASS claim before page_at suppresses the auto-page")


def test_resolve_before_page_is_self_resolved():
    fp = "fp/selfresolve"
    core.handle_firing(_alert(fp, hold=120))
    sig = S.incident_get(fp)["sig"]
    core.handle_resolved({**_alert(fp), "status": "resolved"})

    assert S.incident_get(fp) is None or S.incident_get(fp).get("status") != "firing", \
        "resolved incident should no longer be firing"
    _force_due(fp)
    assert core._reconcile_once() == 0, "a resolved incident must never page"
    samples = S.history_samples(sig)
    assert samples and samples[-1]["self_resolved"] is True, "should record a self-resolve"
    logged = S.read_incidents(3600)
    assert any(r.get("self_resolved") for r in logged), "incident_log should note self-resolve"
    print("PASS resolve during hold → self-resolved, no page, history recorded")


def test_idempotent_ack_fires_pagerduty_once():
    fp = "fp/ack"
    op = core._op_login("bob")
    core.handle_firing(_alert(fp, hold=120))

    before = sum(1 for a in PD if a == ("acknowledge", fp))
    code, res = core._ack(fp, op, idem="k1")
    assert code == 200 and res.get("first") is True, "first ack should report first=True"
    after_first = sum(1 for a in PD if a == ("acknowledge", fp))
    assert after_first == before + 1, "first ack fires exactly one PD acknowledge"

    code, res = core._ack(fp, op, idem="k1")           # replay same idem key
    assert code == 200 and not res.get("first"), "replay must not report first"
    after_replay = sum(1 for a in PD if a == ("acknowledge", fp))
    assert after_replay == after_first, "replayed ack must NOT re-fire PagerDuty"
    print("PASS idempotent ack fires PagerDuty exactly once")


def test_store_ack_first_flag_directly():
    """Unit-level guard on the store contract the reconciler depends on."""
    from nanny.store import MemoryStore
    st = MemoryStore(data_dir=_TMP)
    st.incident_create({"fingerprint": "x", "summary": "s", "sig": "g"})
    _, r1 = st.incident_ack("x", "op1", "op1", "i1")
    assert r1["first"] is True, "fresh ack → first=True"
    _, r2 = st.incident_ack("x", "op1", "op1", "i1")
    assert not r2.get("first"), "idem replay → no first"
    _, r3 = st.incident_ack("x", "op2", "op2", "i2")
    assert r3["first"] is False, "already-acked (new idem) → first=False"
    print("PASS MemoryStore.incident_ack first-flag semantics")


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nOK — {len(tests)} reconciler/lifecycle tests passed (MemoryStore, dry-run).")


if __name__ == "__main__":
    _run_all()
