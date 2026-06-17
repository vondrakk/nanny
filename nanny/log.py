"""nanny.log — peeled from nanny.core (Phase 2)."""

import os
import sys
import json
import socket
import threading
from collections import deque
from datetime import datetime, timezone
from nanny.config import *
from nanny.util import *

__all__ = ['_LOG', '_log_lock', '_log_seq', 'log_event', '_SYSLOG_SEV', '_log_forward', '_log_snapshot', 'log_audit']


def log_audit(name: str, inp: dict, out: str) -> None:
    # Tool-use evidence trail (who/bot looked at what, when). Routed through
    # log_event so it's captured in the activity log AND forwarded to the configured
    # sink (stdout JSON / syslog → SIEM) — see _log_forward. (Audit F11.)
    log_event("AUDIT", f"{name}({_fmt_args(inp) if isinstance(inp, dict) else inp})",
              tool=name, output_preview=str(out)[:200])


_LOG = deque(maxlen=800)


_log_lock = threading.Lock()


_log_seq = 0


def log_event(kind: str, text: str, **extra) -> dict:
    """Record one activity-log line. `kind` groups/colors it (PAGE, TICKET,
    RECOVERED, INVESTIGATING, ACK, PD, DISCOVER, TEST, AUDIT, …). Also forwarded to
    any configured external sink (stdout JSON / syslog) for K8s/SIEM collection."""
    global _log_seq
    with _log_lock:
        _log_seq += 1
        rec = {"id": _log_seq,
               "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "kind": kind, "text": text}
        rec.update(extra)
        _LOG.append(rec)
    _log_forward(rec)                       # outside the lock — never block on I/O
    return rec


_SYSLOG_SEV = {"PAGE": 3, "ERROR": 3, "WARN": 4, "AUTH": 5, "ICINGA": 5,
               "RECOVERED": 6, "TICKET": 6, "INVESTIGATING": 6, "PD": 6,
               "DISCOVER": 6, "HOLD": 6, "TEST": 7, "TRIAGE": 7, "AUDIT": 6}


def _log_forward(rec: dict) -> None:
    if _env_flag("NANNY_LOG_JSON"):
        try:
            sys.stdout.write(json.dumps({"app": "nanny", **rec}) + "\n")
            sys.stdout.flush()
        except Exception:
            pass
    tgt = (os.environ.get("NANNY_LOG_SYSLOG") or "").strip()
    if tgt:
        try:
            host, _, port = tgt.partition(":")
            sev = _SYSLOG_SEV.get(rec.get("kind", ""), 6)
            pri = 16 * 8 + sev                      # facility local0(16) · severity
            msg = (f"<{pri}>1 {rec.get('ts')} nanny nanny - {rec.get('kind')} "
                   f"- [seq={rec.get('id')}] {rec.get('text')}")
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.sendto(msg.encode("utf-8", "replace")[:2000], (host, int(port or 514)))
            s.close()
        except Exception:
            pass


def _log_snapshot(since: int = 0, limit: int = 400) -> list:
    with _log_lock:
        items = [r for r in _LOG if r["id"] > since]
    return items[-limit:]
