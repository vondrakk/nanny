"""nanny.slack — Slack notifications + interactive Block Kit incident workflow.

Two layers sit here:

  1. notify()          plain-text status lines (host monitor: WARN/RECOVERED/PAGE).
  2. incident cards    rich Block Kit messages with Claim / Ack / Release buttons
                       that drive the SAME operator workflow as the web console,
                       received back through a signed /slack/interactive endpoint.

When SLACK_SIGNING_SECRET is set ("interactive mode"), incident lifecycle events
(INVESTIGATING/PAGE/ACK/RECOVERED) are rendered as a single, in-place-updated card
instead of a stream of text lines — so notify() suppresses its text post for those
levels and the card becomes the canonical Slack surface for the incident.
"""

import os
import json
import time
import hmac
import hashlib
from urllib.parse import parse_qs
from slack_sdk import WebClient
from nanny.config import *
from nanny.log import *
from nanny.ntfy import ntfy_send

__all__ = ['notify', 'slack_enabled', 'cards_enabled', 'incident_blocks',
           'post_incident', 'verify_slack_signature', 'parse_interaction',
           'SLACK_REPLAY_WINDOW', 'SLACK_ACTIONS']

# Signed Slack requests older than this are rejected (replay guard, per Slack docs).
SLACK_REPLAY_WINDOW = 300

# action_id -> the operator verb it maps to. The web console and Slack share the
# exact same _claim/_ack/_release primitives; this is just the naming bridge.
SLACK_ACTIONS = {"nanny_claim": "claim", "nanny_ack": "ack", "nanny_release": "release"}

# Incident lifecycle levels that the interactive card represents; notify() skips
# its Slack text post for these when cards are enabled (the card replaces them).
_CARD_LEVELS = {"INVESTIGATING", "PAGE", "ACK", "RECOVERED"}

_SEVERITY_EMOJI = {"critical": ":rotating_light:", "high": ":rotating_light:",
                   "warning": ":warning:", "warn": ":warning:",
                   "info": ":information_source:"}


def slack_enabled() -> bool:
    """True when nanny can post to Slack at all (bot token + target channel)."""
    return bool(os.environ.get("SLACK_BOT_TOKEN") and os.environ.get("SLACK_ALERT_CHANNEL"))


def cards_enabled() -> bool:
    """True when interactive cards are in play. We require a signing secret because
    without it we can't verify the button clicks coming back, so plain text is safer."""
    return bool(os.environ.get("SLACK_SIGNING_SECRET"))


def _client() -> WebClient:
    return WebClient(token=os.environ["SLACK_BOT_TOKEN"])


def _http_url(u) -> bool:
    """Only surface http(s) links in Slack (no javascript:/data: smuggled via notes_url)."""
    return isinstance(u, str) and (u.startswith("https://") or u.startswith("http://"))


def notify(level: str, text: str):
    color = {"WARN": "33", "PAGE": "1;31", "RECOVERED": "32",
             "INVESTIGATING": "36", "ACK": "1;34"}.get(level, "0")
    print(_c(f"[{level}] ", color) + text)
    log_event(level, text)
    # ntfy is the demo's real channel — fires even in dry-run (no live on-call is
    # woken). No-op unless a topic is set, so real deployments are unaffected. PAGE is
    # sent separately from _page_incident so it can carry interactive action buttons.
    if level != "PAGE":
        ntfy_send(level, text)
    if _dry():
        return                                       # log only; no real Slack post
    if not slack_enabled():
        return
    if cards_enabled() and level in _CARD_LEVELS:
        return                                       # the incident card carries these
    try:
        _client().chat_postMessage(channel=os.environ["SLACK_ALERT_CHANNEL"],
                                   text=f"[{level}] {text}")
    except Exception as e:
        print(_c(f"slack: notify failed: {e}", "2"))


def _button(text: str, action_id: str, value: str, style: str = None) -> dict:
    b = {"type": "button", "action_id": action_id,
         "text": {"type": "plain_text", "text": text, "emoji": True},
         "value": (value or "")[:2000]}             # Slack caps button value at 2000 chars
    if style:
        b["style"] = style
    return b


def incident_blocks(inc: dict) -> list:
    """Build the Block Kit message for an incident. `inc` is a plain dict of fields
    (works with both a live incident record and a snapshot) — read defensively so the
    same builder serves create / update / resolved states.

    Buttons carry the incident fingerprint in `value`; we offer only the moves that
    make sense for the current state (no Claim once owned, no Ack once acked, etc.)."""
    sev = (inc.get("severity") or "critical").lower()
    summary = inc.get("summary") or inc.get("sig") or "incident"
    phase = inc.get("phase", "firing")
    owner = inc.get("owner_name") or inc.get("owner")
    acked = bool(inc.get("acked"))
    paged = bool(inc.get("paged"))
    fp = inc.get("id") or inc.get("fingerprint") or ""
    emoji = _SEVERITY_EMOJI.get(sev, ":rotating_light:")

    if phase == "resolved":
        status = ":white_check_mark: *Resolved*"
    elif acked:
        status = ":eyes: *Acknowledged*" + (f" by {owner}" if owner else "")
    elif owner:
        status = f":raising_hand: *Claimed* by {owner}"
    elif paged:
        status = ":rotating_light: *Paged on-call*"
    elif phase == "holding":
        status = f":hourglass_flowing_sand: Holding {inc.get('hold', 0)}s before paging"
    else:
        status = ":mag: Investigating"

    blocks = [
        {"type": "header",
         "text": {"type": "plain_text", "text": f"{emoji} {summary[:140]}", "emoji": True}},
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*Host*\n{inc.get('instance') or '—'}"},
            {"type": "mrkdwn", "text": f"*Service*\n{inc.get('service') or '—'}"},
            {"type": "mrkdwn", "text": f"*Severity*\n{sev}"},
            {"type": "mrkdwn", "text": f"*Status*\n{status}"},
        ]},
    ]

    links = []
    rb = inc.get("runbook")
    if _http_url(rb):
        links.append(f"<{rb}|:book: Runbook>")
    key = inc.get("key") or inc.get("ticket")
    base = os.environ.get("JIRA_BASE_URL", "").rstrip("/")
    if key and base:
        links.append(f"<{base}/browse/{key}|:jira: {key}>")
    elif key:
        links.append(f":jira: {key}")
    if links:
        blocks.append({"type": "context",
                       "elements": [{"type": "mrkdwn", "text": "   ·   ".join(links)}]})

    if phase != "resolved":
        actions = []
        if not owner:
            actions.append(_button("Claim", "nanny_claim", fp, "primary"))
        if not acked:
            actions.append(_button("Ack", "nanny_ack", fp, "primary" if owner else None))
        if owner:
            actions.append(_button("Release", "nanny_release", fp, "danger"))
        if actions:
            blocks.append({"type": "actions", "block_id": "nanny_inc", "elements": actions})
    return blocks


def post_incident(inc: dict):
    """Upsert the incident's Slack card. First call posts and stamps inc['slack_ts'];
    later calls chat_update the same message in place. Returns the message ts (or the
    existing one) — or None when Slack isn't configured / dry-run.

    Best-effort by contract: a Slack failure must never break incident handling."""
    sev = (inc.get("severity") or "critical").upper()
    fallback = f"[{sev}] {inc.get('summary', 'incident')}"
    if _dry() or not slack_enabled():
        return inc.get("slack_ts")
    channel = os.environ["SLACK_ALERT_CHANNEL"]
    try:
        c = _client()
        blocks = incident_blocks(inc)
        if inc.get("slack_ts"):
            c.chat_update(channel=channel, ts=inc["slack_ts"], blocks=blocks, text=fallback)
            return inc["slack_ts"]
        r = c.chat_postMessage(channel=channel, blocks=blocks, text=fallback)
        inc["slack_ts"] = r.get("ts")
        return inc["slack_ts"]
    except Exception as e:
        print(_c(f"slack: incident card failed: {e}", "2"))
        return inc.get("slack_ts")


def verify_slack_signature(headers, raw, now: float = None) -> bool:
    """Verify a Slack request signature (v0 scheme: HMAC-SHA256 over
    `v0:{timestamp}:{raw_body}` with the app signing secret). Rejects missing secret,
    stale timestamps (replay), and mismatched signatures — constant-time compared.

    `headers` is anything with a case-insensitive .get (BaseHTTPRequestHandler.headers
    qualifies). `raw` is the exact request body bytes — must NOT be re-serialized."""
    secret = os.environ.get("SLACK_SIGNING_SECRET")
    if not secret:
        return False
    ts = headers.get("X-Slack-Request-Timestamp", "") or ""
    sig = headers.get("X-Slack-Signature", "") or ""
    if not ts or not sig.startswith("v0="):
        return False
    try:
        ts_i = int(ts)
    except (ValueError, TypeError):
        return False
    now = time.time() if now is None else now
    if abs(now - ts_i) > SLACK_REPLAY_WINDOW:
        return False
    if isinstance(raw, str):
        raw = raw.encode()
    base = b"v0:" + ts.encode() + b":" + (raw or b"")
    want = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, want)


def parse_interaction(raw):
    """Parse a Slack interactivity POST body (form-encoded with a JSON `payload`
    field) into a normalized dict, or None if it can't be parsed. We only look at the
    first action — our cards carry a single button press per interaction."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    payloads = parse_qs(raw).get("payload")
    if not payloads:
        return None
    try:
        p = json.loads(payloads[0])
    except (ValueError, TypeError):
        return None
    action = (p.get("actions") or [{}])[0]
    user = p.get("user") or {}
    return {
        "action": action.get("action_id", ""),
        "value": action.get("value", ""),
        "user_id": user.get("id", ""),
        "user_name": user.get("username") or user.get("name") or user.get("id", "") or "slack-user",
        "response_url": p.get("response_url", ""),
        "trigger_id": p.get("trigger_id", ""),
        "ts": (p.get("message") or {}).get("ts", ""),
        "channel": (p.get("channel") or {}).get("id", ""),
    }
