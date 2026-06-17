"""nanny.pagerduty — peeled from nanny.core (Phase 2)."""

import os
import re
import requests
from datetime import datetime, timezone
from nanny.config import *
from nanny.log import *

__all__ = ['_PD_INCIDENT_RE', 'acknowledge_pagerduty', 'pd_event']


_PD_INCIDENT_RE = re.compile(r"pagerduty\.com/incidents/([A-Z0-9]+)", re.I)


def acknowledge_pagerduty(alert_text: str) -> None:
    """Acknowledge the incident so PagerDuty stops escalating/paging.

    We find the incident id in the Slack alert text (PD's messages embed the
    incident URL). If there's no id, we skip rather than fail the triage.
    """
    match = _PD_INCIDENT_RE.search(alert_text or "")
    if not match:
        print("pagerduty: no incident id in alert text — skipping ack.")
        return
    incident_id = match.group(1)
    base = os.environ.get("PAGERDUTY_API_BASE", "https://api.pagerduty.com").rstrip("/")
    try:
        r = requests.put(
            f"{base}/incidents/{incident_id}",
            json={"incident": {"type": "incident_reference", "status": "acknowledged"}},
            headers={
                "Authorization": f"Token token={os.environ['PAGERDUTY_API_TOKEN']}",
                "From": os.environ["PAGERDUTY_FROM_EMAIL"],   # required, else 400
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=15,
        )
        if r.ok:
            print(f"pagerduty: acknowledged {incident_id}")
        else:
            print(f"pagerduty: ack failed for {incident_id} "
                  f"({r.status_code}): {r.text[:200]}")
    except requests.RequestException as e:
        print(f"pagerduty: ack request error for {incident_id}: {e}")


def pd_event(action: str, dedup_key: str, summary: str,
             severity: str = "critical", source: str = "nanny"):
    """Trigger/resolve a PagerDuty incident via Events API v2 (routing key)."""
    log_event("PD", f"PagerDuty {action} · {dedup_key}")
    if _dry():
        log_event("PD", f"[dry-run] suppressed PagerDuty {action} for {dedup_key}")
        return
    routing = os.environ.get("PAGERDUTY_ROUTING_KEY")
    if not routing:
        print(_c(f"pagerduty: no PAGERDUTY_ROUTING_KEY — would {action} '{dedup_key}'", "2"))
        return
    body = {"routing_key": routing, "event_action": action, "dedup_key": dedup_key}
    if action == "trigger":
        body["payload"] = {"summary": summary[:1024], "source": source,
                           "severity": severity,
                           "timestamp": datetime.now(timezone.utc).isoformat()}
    try:
        r = requests.post("https://events.pagerduty.com/v2/enqueue", json=body, timeout=10)
        if not r.ok:
            print(_c(f"pagerduty: {action} failed ({r.status_code}): {r.text[:200]}", "31"))
    except requests.RequestException as e:
        print(_c(f"pagerduty: {action} error: {e}", "31"))
