"""Configuration: constants, environment helpers, and small JSON/IO + auth/LLM mode predicates. Imported by every other nanny module; depends only on stdlib."""

import os
import sys
import json

__all__ = ['SVC_USER', 'WRAPPER_PATH', 'NANNY_VERSION', 'PROJECT_BLURB', 'AUTHOR', 'MODEL', 'ALERT_BOT_ID', 'MAX_TURNS', 'MAX_LOG_LINES', 'MAX_BODY_BYTES', 'LOG_ALLOWLIST', '_log_allowlist', '_c', '_env_flag', '_icinga_configured', '_llm_backend', '_llm_enabled', '_env_dir', '_data_path', '_load_json', '_save_json', '_dry', '_demo', '_auth_mode', '_auth_enabled', '_auth_open_allowed']


SVC_USER = "nanny"


WRAPPER_PATH = "/usr/local/bin/nanny-logreader"


NANNY_VERSION = "1.0"


PROJECT_BLURB = ("Supervised, audited on-call automation: discover -> monitor -> "
                 "triage -> adaptive paging -> shift reports.")


AUTHOR = {
    "name": "Viktor",                                  # <- your name as shown
    "title": "Director of Operations, DomainTools",
    "linkedin": "https://www.linkedin.com/in/your-handle",  # <- your LinkedIn URL
}


MODEL = "claude-opus-4-8"


ALERT_BOT_ID = os.environ.get("PAGERDUTY_SLACK_BOT_ID", "")


MAX_TURNS = 12          # hard cap on the agentic loop


MAX_LOG_LINES = 500     # ceiling on how much log the bot can pull in one call


MAX_BODY_BYTES = 256 * 1024     # reject HTTP request bodies larger than this (DoS guard)


LOG_ALLOWLIST = {
    # "web-01": ["/var/log/app/app.log"],   # optional manual entries
}


def _log_allowlist() -> dict:
    """Effective allowlist: the in-code seed merged with what `install`
    discovered on each host (persisted to NANNY_DATA_DIR/log_allowlist.json)."""
    merged = {h: list(p) for h, p in LOG_ALLOWLIST.items()}
    try:
        for h, paths in json.load(open(_data_path("log_allowlist.json"))).items():
            merged.setdefault(h, [])
            for p in paths:
                if p not in merged[h]:
                    merged[h].append(p)
    except (OSError, ValueError):
        pass
    return merged


def _c(text: str, code: str) -> str:
    """Wrap text in an ANSI code, but only when writing to a real terminal."""
    return f"\033[{code}m{text}\033[0m" if sys.stdout.isatty() else text


def _llm_backend() -> str:
    return os.environ.get("NANNY_LLM_BACKEND", "anthropic").lower()


def _llm_enabled() -> bool:
    """False in no-LLM mode (NANNY_LLM_BACKEND=none): nanny still triages, tickets
    and pages — it just gathers evidence deterministically instead of reasoning."""
    return _llm_backend() not in ("none", "off", "disabled", "no")


def _env_flag(key: str) -> bool:
    return (os.environ.get(key) or "").split("#", 1)[0].strip().lower() in (
        "1", "true", "yes", "on")


def _icinga_configured() -> bool:
    return bool(_env_dir("ICINGA_API_URL", ""))


def _dry() -> bool:
    """Test mode: intercept external side effects (Jira / PagerDuty / Slack) and log
    what *would* happen instead of doing it. Enabled by NANNY_DRY_RUN."""
    return _env_flag("NANNY_DRY_RUN")


def _demo() -> bool:
    """Self-contained demo mode (NANNY_DEMO): drives the full lifecycle with synthetic
    alerts, an ntfy channel instead of Slack/PagerDuty, a fabricated fleet, and dry-run
    side effects — so it runs end-to-end with no real integrations or secrets."""
    return _env_flag("NANNY_DEMO")


def _env_dir(key: str, default: str) -> str:
    """Read a directory/path env var, tolerant of the `podman --env-file`
    footgun where an inline `# comment` becomes part of the value."""
    raw = os.environ.get(key) or default
    return raw.split("#", 1)[0].strip() or default


def _data_path(name: str) -> str:
    return os.path.join(_env_dir("NANNY_DATA_DIR", "data"), name)


def _load_json(name: str, default):
    try:
        return json.load(open(_data_path(name)))
    except (OSError, ValueError):
        return default


def _save_json(name: str, obj):
    p = _data_path(name)
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    with open(p, "w") as fh:
        json.dump(obj, fh)


def _auth_mode() -> str:
    if (os.environ.get("NANNY_LDAP_URL") or "").strip():
        return "ldap"
    if (os.environ.get("NANNY_AUTH_PASSWORD") or "").strip():
        return "password"
    return "open"


def _auth_enabled() -> bool:
    """True when a credential (LDAP bind or shared password) is required to log in.
    Drives the console's password field + idle-logout enforcement."""
    return _auth_mode() != "open"


def _auth_open_allowed() -> bool:
    """Name-only access is allowed only as a deliberate, logged opt-in."""
    return _env_flag("NANNY_AUTH_INSECURE")
