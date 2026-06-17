"""Unit tests for the interactive Slack layer (nanny.slack).

Pure-logic tests — no network: Block Kit construction across incident states,
Slack request-signature verification (good/forged/stale/missing-secret), and
interaction-payload parsing. Run inside the bundle image:  python -m tests.test_slack
"""
import os
import json
import time
import hmac
import hashlib
from urllib.parse import urlencode

os.environ.setdefault("SLACK_BOT_TOKEN", "xoxb-test")
os.environ.setdefault("SLACK_ALERT_CHANNEL", "C123")
os.environ["SLACK_SIGNING_SECRET"] = "8f742231b10e8888abcd99yyyzzz85a5"  # Slack's doc example

from nanny import slack


class _Headers(dict):
    """Case-insensitive .get, like http.server's headers."""
    def get(self, k, default=None):
        for key, val in self.items():
            if key.lower() == k.lower():
                return val
        return default


def _sign(secret, ts, body):
    base = f"v0:{ts}:{body}".encode()
    return "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()


passed = failed = 0
def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}")


# ── incident_blocks across states ─────────────────────────────────────────────
def _types(blocks):
    return [b["type"] for b in blocks]

def _action_ids(blocks):
    for b in blocks:
        if b["type"] == "actions":
            return [e["action_id"] for e in b["elements"]]
    return []

base_inc = {"id": "host/svc", "summary": "disk full on web-01", "sig": "DiskFull",
            "severity": "critical", "instance": "web-01", "service": "disk",
            "runbook": "https://confluence.example/runbook/disk",
            "key": "OPS-42", "phase": "firing", "owner_name": None,
            "acked": False, "paged": False}

fresh = slack.incident_blocks(base_inc)
check("fresh: has header+section", _types(fresh)[:2] == ["header", "section"])
check("fresh: offers Claim and Ack", set(_action_ids(fresh)) == {"nanny_claim", "nanny_ack"})
check("fresh: runbook+ticket context block present", "context" in _types(fresh))

claimed = slack.incident_blocks({**base_inc, "owner_name": "viktor", "phase": "claimed"})
check("claimed: no Claim button", "nanny_claim" not in _action_ids(claimed))
check("claimed: offers Ack + Release", set(_action_ids(claimed)) == {"nanny_ack", "nanny_release"})

acked = slack.incident_blocks({**base_inc, "owner_name": "viktor", "acked": True})
check("acked: only Release remains", _action_ids(acked) == ["nanny_release"])

resolved = slack.incident_blocks({**base_inc, "phase": "resolved", "acked": True,
                                  "owner_name": "viktor"})
check("resolved: no actions block", "actions" not in _types(resolved))

# javascript: runbook must not become a link
evil = slack.incident_blocks({**base_inc, "runbook": "javascript:alert(1)"})
evil_text = json.dumps(evil)
check("safe links: javascript runbook dropped", "javascript:" not in evil_text)

# value carries the fingerprint
for b in fresh:
    if b["type"] == "actions":
        check("button value carries fingerprint", b["elements"][0]["value"] == "host/svc")


# ── signature verification ────────────────────────────────────────────────────
secret = os.environ["SLACK_SIGNING_SECRET"]
body = "payload=%7B%7D"
ts = str(int(time.time()))
good = _sign(secret, ts, body)

check("sig: valid signature accepted",
      slack.verify_slack_signature(_Headers({"X-Slack-Request-Timestamp": ts,
                                              "X-Slack-Signature": good}), body))
check("sig: forged signature rejected",
      not slack.verify_slack_signature(_Headers({"X-Slack-Request-Timestamp": ts,
                                                 "X-Slack-Signature": "v0=deadbeef"}), body))
check("sig: stale timestamp rejected (replay guard)",
      not slack.verify_slack_signature(
          _Headers({"X-Slack-Request-Timestamp": str(int(ts) - 9999),
                    "X-Slack-Signature": _sign(secret, str(int(ts) - 9999), body)}), body))
check("sig: tampered body rejected",
      not slack.verify_slack_signature(_Headers({"X-Slack-Request-Timestamp": ts,
                                                 "X-Slack-Signature": good}), body + "x"))
check("sig: bytes body verifies the same as str",
      slack.verify_slack_signature(_Headers({"X-Slack-Request-Timestamp": ts,
                                             "X-Slack-Signature": good}), body.encode()))
check("sig: missing headers rejected",
      not slack.verify_slack_signature(_Headers({}), body))

# missing secret → always reject
del os.environ["SLACK_SIGNING_SECRET"]
check("sig: no signing secret → reject",
      not slack.verify_slack_signature(_Headers({"X-Slack-Request-Timestamp": ts,
                                                 "X-Slack-Signature": good}), body))
check("cards: disabled when secret unset", not slack.cards_enabled())
os.environ["SLACK_SIGNING_SECRET"] = secret
check("cards: enabled when secret set", slack.cards_enabled())


# ── interaction parsing ───────────────────────────────────────────────────────
payload = {
    "type": "block_actions",
    "user": {"id": "U99", "username": "viktor"},
    "channel": {"id": "C123"},
    "message": {"ts": "1700000000.000100"},
    "response_url": "https://hooks.slack.com/actions/abc",
    "actions": [{"action_id": "nanny_ack", "value": "web-01/disk"}],
}
raw = urlencode({"payload": json.dumps(payload)})
intr = slack.parse_interaction(raw)
check("parse: action_id extracted", intr["action"] == "nanny_ack")
check("parse: value (fingerprint) extracted", intr["value"] == "web-01/disk")
check("parse: user id + name", (intr["user_id"], intr["user_name"]) == ("U99", "viktor"))
check("parse: message ts + channel", intr["ts"] == "1700000000.000100" and intr["channel"] == "C123")
check("parse: bytes body works", slack.parse_interaction(raw.encode())["action"] == "nanny_ack")
check("parse: garbage → None", slack.parse_interaction("not-a-form") is None)
check("parse: empty → None", slack.parse_interaction("") is None)
check("actions: SLACK_ACTIONS maps all three verbs",
      set(slack.SLACK_ACTIONS.values()) == {"claim", "ack", "release"})


print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
