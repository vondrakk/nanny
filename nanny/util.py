"""Pure, dependency-free helpers (formatting, escaping, Jira ADF, signatures)."""

import re
import sys
import time

__all__ = ['_trunc', '_fmt_perf', '_fmt_args', '_strip_html', '_quote_escape', '_ldap_esc', '_icinga_age', '_adf_text', '_adf_with_codeblock', '_signature', '_parse_window']


def _strip_html(text: str) -> str:
    # Confluence excerpts come back as HTML with @@@hl@@@ highlight markers.
    text = re.sub(r"@@@(end)?hl@@@", "", text or "")
    return re.sub(r"<[^>]+>", "", text).strip()


def _quote_escape(s: str) -> str:
    # Escape double quotes for use inside a quoted CQL/JQL string value.
    return s.replace('"', '\\"')


def _adf_text(text: str) -> dict:
    """Plain text -> Atlassian Document Format (Jira Cloud v3 requires ADF)."""
    content = [{"type": "paragraph", "content": [{"type": "text", "text": line}]}
               for line in text.splitlines() if line.strip()]
    return {"type": "doc", "version": 1, "content": content or
            [{"type": "paragraph", "content": []}]}


def _adf_with_codeblock(intro: str, code: str) -> dict:
    doc = _adf_text(intro)
    doc["content"].append({"type": "codeBlock", "attrs": {"language": "text"},
                           "content": [{"type": "text", "text": code or "(none)"}]})
    return doc


def _fmt_args(d: dict) -> str:
    """Compact one-line rendering of tool args for the shell trace."""
    bits = []
    for k, v in d.items():
        s = str(v)
        bits.append(f"{k}={s[:60] + '…' if len(s) > 60 else s}")
    return ", ".join(bits)


def _icinga_age(ts) -> int:
    """Whole seconds since an Icinga epoch timestamp (None if absent/garbage)."""
    try:
        return max(0, int(time.time() - float(ts))) if ts else None
    except (TypeError, ValueError):
        return None


def _trunc(s, n) -> str:
    s = (str(s) if s is not None else "").replace("\n", " ").strip()
    return s[:n] + ("…" if len(s) > n else "")


def _fmt_perf(pd) -> str:
    if not pd:
        return ""
    return " ".join(str(x) for x in pd) if isinstance(pd, (list, tuple)) else str(pd)


def _signature(labels: dict) -> str:
    return f"{labels.get('alertname', '?')}/{labels.get('service', '?')}"


def _ldap_esc(s: str) -> str:
    """Escape RFC-4515 filter metacharacters so a username can't smuggle a filter."""
    for a, b in (("\\", "\\5c"), ("*", "\\2a"), ("(", "\\28"),
                 (")", "\\29"), ("\x00", "\\00")):
        s = s.replace(a, b)
    return s


def _parse_window(s: str) -> int:
    m = re.fullmatch(r"(\d+)\s*([smhd])", s.strip())
    if not m:
        sys.exit("report: --since must look like 90m, 8h, or 1d")
    return int(m.group(1)) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]
