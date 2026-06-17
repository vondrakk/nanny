"""nanny.ntfy — push notifications + interactive paging via ntfy (https://ntfy.sh).

ntfy is the demo's communication channel: a zero-account, HTTP pub/sub notifier you
subscribe to from a phone/browser. It replaces Slack + PagerDuty for a self-contained
demo — no bot tokens, no routing keys, no signing secrets.

Two layers, mirroring slack.py:

  1. ntfy_send()            level-styled pushes (INVESTIGATING/WARN/RECOVERED/ACK)
                            wired into notify(); PAGE is sent from _page_incident so
                            it can carry action buttons.
  2. action buttons         a PAGE push carries Ack / Claim / Console buttons (ntfy
                            "http" actions) that POST back to nanny's own API, so an
                            operator can drive the incident from the lock screen.

Everything here is best-effort and fires even in dry-run: ntfy is a safe demo surface,
so unlike Slack/PagerDuty we do NOT suppress it under NANNY_DRY_RUN. It is a complete
no-op unless NANNY_NTFY_TOPIC is set, so configured-but-real deployments are unaffected.
"""

import os
import json
import requests
from urllib.parse import quote
from nanny.config import *
from nanny.log import *

__all__ = ['ntfy_enabled', 'ntfy_interactive', 'ntfy_send', 'ntfy_incident_actions',
           'ntfy_base', 'ntfy_topic']

# level -> (priority 1-5, [tags/emoji short-codes]). ntfy renders tags that match an
# emoji short-code as the notification icon, the rest as text labels.
_LEVELS = {
    "PAGE":          (5, ["rotating_light", "pager"]),
    "WARN":          (4, ["warning"]),
    "ACK":           (4, ["eyes"]),
    "INVESTIGATING": (3, ["mag"]),
    "RECOVERED":     (2, ["white_check_mark"]),
}
_DEFAULT = (3, ["robot_face"])


def ntfy_base() -> str:
    """Server root, e.g. https://ntfy.sh or http://ntfy (self-hosted). Tolerant of the
    `podman --env-file` inline-comment footgun via _env_dir."""
    return _env_dir("NANNY_NTFY_URL", "https://ntfy.sh").rstrip("/")


def ntfy_topic() -> str:
    return _env_dir("NANNY_NTFY_TOPIC", "")


def ntfy_enabled() -> bool:
    """True once a topic is set — the single switch that turns the channel on."""
    return bool(ntfy_topic())


def ntfy_interactive() -> bool:
    """True when PAGE pushes can carry action buttons that call back into nanny. The
    callback base (NANNY_PUBLIC_URL) must be reachable FROM THE SUBSCRIBER'S DEVICE
    (the ntfy app runs the HTTP action, not the server), and a token must exist to
    authenticate the call (pre-seeded as a session by demo startup)."""
    return bool(ntfy_enabled()
                and _env_dir("NANNY_PUBLIC_URL", "")
                and os.environ.get("NANNY_DEMO_ACTION_TOKEN"))


def ntfy_send(level: str, text: str, *, title: str = None,
              actions: list = None, click: str = None) -> bool:
    """Publish one notification. Best-effort: returns False (never raises) on any
    failure, so a notifier hiccup can't break incident handling. No-op when disabled."""
    if not ntfy_enabled():
        return False
    prio, tags = _LEVELS.get(level, _DEFAULT)
    payload = {
        "topic": ntfy_topic(),
        "message": (text or "")[:3500],
        "title": title or f"nanny · {level}",
        "priority": prio,
        "tags": tags,
    }
    if actions:
        payload["actions"] = actions[:3]      # ntfy caps a message at 3 actions
    if click:
        payload["click"] = click
    headers = {}
    token = os.environ.get("NANNY_NTFY_TOKEN")     # only needed for protected topics
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        # JSON publishing posts to the server ROOT (topic travels in the body), which
        # keeps URLs/actions free of the header-DSL escaping the GET form requires.
        r = requests.post(ntfy_base() + "/", json=payload, headers=headers, timeout=8)
        if not r.ok:
            log_event("NTFY", f"publish failed ({r.status_code}): {r.text[:160]}")
            return False
        return True
    except requests.RequestException as e:
        log_event("NTFY", f"publish error: {type(e).__name__}: {e}")
        return False


def ntfy_incident_actions(fp: str, *, owned: bool = False, acked: bool = False) -> list:
    """Build the ntfy action buttons for an incident push: Ack / Claim / Console.

    Each "http" action is executed by the subscriber's ntfy app and POSTs to nanny's
    existing operator endpoints with the demo bearer token, so a phone tap drives the
    same claim/ack workflow as the web console. Returns [] when not interactive."""
    if not ntfy_interactive():
        return []
    base = _env_dir("NANNY_PUBLIC_URL", "").rstrip("/")
    token = os.environ["NANNY_DEMO_ACTION_TOKEN"]
    fpq = quote(fp or "", safe="")                 # fingerprints contain '/'
    hdrs = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    acts = []
    if not acked:
        acts.append({"action": "http", "label": "Ack",
                     "url": f"{base}/api/incidents/{fpq}/ack",
                     "method": "POST", "headers": hdrs, "body": "{}", "clear": True})
    if not owned:
        acts.append({"action": "http", "label": "Claim",
                     "url": f"{base}/api/incidents/{fpq}/claim",
                     "method": "POST", "headers": hdrs, "body": "{}"})
    acts.append({"action": "view", "label": "Console", "url": base or ntfy_base()})
    return acts
