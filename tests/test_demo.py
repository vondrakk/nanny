"""Demo-mode tests: the ntfy channel + the self-contained demo lifecycle.

Runs offline with no third-party deps: `requests` is stubbed with a recorder that
captures every outbound POST (so we can assert on the ntfy publishes), and the other
heavy deps are stubbed before importing nanny.core. Backed by MemoryStore (DATABASE_URL
unset), dry-run on. Run: `python tests/test_demo.py` (also importable under pytest).
"""

import os
import sys
import time
import types
import tempfile

# ── isolate state + demo/ntfy env BEFORE importing nanny.core ──────────────────
_TMP = tempfile.mkdtemp(prefix="nanny-demo-test-")
os.environ["NANNY_DATA_DIR"] = _TMP
os.environ["NANNY_DRY_RUN"] = "true"
os.environ.pop("DATABASE_URL", None)
os.environ["NANNY_DEMO"] = "true"
os.environ["NANNY_NTFY_TOPIC"] = "nanny-demo-test"
os.environ["NANNY_PUBLIC_URL"] = "http://10.0.0.9:9099"
os.environ["NANNY_DEMO_ACTION_TOKEN"] = "tok-test"

# Every outbound POST lands here so we can assert on the ntfy publishes.
POSTS = []


def _stub(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


class _Resp:
    ok = True
    status_code = 200
    text = "ok"

    def json(self):
        return {}


def _post(url, json=None, headers=None, timeout=None, **kw):
    POSTS.append({"url": url, "json": json, "headers": headers})
    return _Resp()


class _ReqError(Exception):
    pass


_stub("requests", post=_post, get=lambda *a, **k: _Resp(), put=_post,
      RequestException=_ReqError)
_stub("anthropic", Anthropic=object)
_stub("slack_sdk", WebClient=object)
_sb = _stub("slack_bolt", App=object)
_ad = _stub("slack_bolt.adapter")
_sm = _stub("slack_bolt.adapter.socket_mode", SocketModeHandler=object)
_sb.adapter = _ad
_ad.socket_mode = _sm

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nanny import core      # noqa: E402
from nanny import ntfy      # noqa: E402

core._demo_seed_session()   # the ntfy action-button token resolves as a session


def _ntfy_posts():
    """Only the ntfy publishes (POST to server root with a topic in the body)."""
    return [p for p in POSTS if p["url"].endswith("/") and (p["json"] or {}).get("topic")]


def _by_priority(p):
    return [c for c in _ntfy_posts() if c["json"]["priority"] == p]


def test_ntfy_disabled_is_noop():
    """No topic → the channel is completely silent (real deployments unaffected)."""
    saved = os.environ.pop("NANNY_NTFY_TOPIC")
    before = len(POSTS)
    try:
        assert ntfy.ntfy_enabled() is False
        assert ntfy.ntfy_send("PAGE", "should not send") is False
        assert len(POSTS) == before, "disabled ntfy must not emit any request"
    finally:
        os.environ["NANNY_NTFY_TOPIC"] = saved
    print("PASS ntfy is a no-op when no topic is configured")


def test_incident_actions_callback_wiring():
    """Page buttons POST back to nanny's own endpoints with the demo bearer token."""
    assert ntfy.ntfy_interactive() is True
    acts = ntfy.ntfy_incident_actions("icinga/db-02.demo/replication-lag")
    assert [a["label"] for a in acts] == ["Ack", "Claim", "Console"]
    ack = acts[0]
    assert ack["method"] == "POST" and ack["url"].endswith("/ack")
    assert "%2F" in ack["url"], "the '/'-bearing fingerprint must be percent-encoded"
    assert ack["headers"]["Authorization"] == "Bearer tok-test"
    print("PASS ntfy action buttons carry the callback URL + auth token")


def test_demo_fleet_overlays_active_incidents():
    """The fabricated fleet renders, and a firing incident turns its row CRIT."""
    core._dispatch_alert(core._demo_fire("db-02.demo", "replication-lag", "lag 240s", 30))
    fleet = core._fleet_status()
    row = next(r for r in fleet if r["instance"] == "db-02.demo"
               and r["service"] == "replication-lag")
    assert row["state"] == 2 and row["up"] is False, "firing service should be CRIT"
    core._dispatch_alert(core._demo_resolve("db-02.demo", "replication-lag", "ok"))
    print("PASS demo fleet populates and overlays firing incidents as CRIT")


def test_lifecycle_page_and_self_resolve():
    """Full path: fire → triage (DRY ticket) → hold → page (interactive ntfy), and a
    separate incident self-resolves before its hold with NO page. Exactly-once page."""
    base = len(_by_priority(5))
    # A: long hold → will self-resolve before paging.
    core._dispatch_alert(core._demo_fire("web-01.demo", "api-5xx", "5xx 38%", 60))
    # B: real hold, then force it due so the reconciler pages it.
    core._dispatch_alert(core._demo_fire("db-02.demo", "replication-lag", "lag 240s", 12))
    assert len(_by_priority(3)) >= 2, "both firings should emit INVESTIGATING pushes"

    core.STORE.incident_update("icinga/db-02.demo/replication-lag", page_at=time.time() - 1)
    assert core._reconcile_once() == 1, "B's hold elapsed → exactly one page"
    pages = _by_priority(5)
    assert len(pages) == base + 1, "exactly one new PAGE push"
    pg = pages[-1]["json"]
    assert "PAGE" in pg["title"] and len(pg.get("actions", [])) == 3

    # A self-resolves before its 60s hold → no page, a RECOVERED push instead.
    n_pages = len(_by_priority(5))
    rec_before = len(_by_priority(2))
    core._dispatch_alert(core._demo_resolve("web-01.demo", "api-5xx", "ok"))
    assert len(_by_priority(5)) == n_pages, "self-resolve must not page"
    assert len(_by_priority(2)) > rec_before, "self-resolve emits a RECOVERED push"
    assert core._reconcile_once() == 0, "no incident is due → no further pages"
    print("PASS full lifecycle: page is interactive ntfy + exactly-once; self-resolve never pages")


def test_ntfy_in_integration_registry():
    names = [n for n, *_ in core._integrations()]
    assert "ntfy" in names, names
    print("PASS ntfy is registered as a testable integration")


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nOK — {len(tests)} demo/ntfy tests passed (MemoryStore, dry-run).")


if __name__ == "__main__":
    _run_all()
