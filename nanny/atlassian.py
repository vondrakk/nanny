"""nanny.atlassian — peeled from nanny.core (Phase 2)."""

import os
import json
import uuid
import requests
from nanny.config import *
from nanny.util import *
from nanny.log import *

__all__ = ['_atlassian_auth', 'search_confluence', 'search_jira', 'create_jira_issue', 'add_jira_comment', '_jira_transition', '_jira_close']


def _atlassian_auth():
    return (os.environ["ATLASSIAN_EMAIL"], os.environ["ATLASSIAN_API_TOKEN"])


def search_confluence(query: str, limit: int = 5) -> str:
    """CQL full-text search over Confluence pages (Cloud v1 search endpoint)."""
    base = os.environ["CONFLUENCE_BASE_URL"].rstrip("/")
    cql = f'type = page AND text ~ "{_quote_escape(query)}"'
    r = requests.get(
        f"{base}/rest/api/search",
        params={"cql": cql, "limit": limit},
        auth=_atlassian_auth(),
        headers={"Accept": "application/json"},
        timeout=15,
    )
    r.raise_for_status()
    hits = []
    for item in r.json().get("results", []):
        content = item.get("content") or {}
        webui = (content.get("_links") or {}).get("webui") or item.get("url") or ""
        hits.append({
            "title": item.get("title") or content.get("title", "(untitled)"),
            "url": f"{base}{webui}" if webui.startswith("/") else webui,
            "excerpt": _strip_html(item.get("excerpt", "")),
        })
    return json.dumps(hits, indent=2) if hits else "No matching Confluence pages."


def search_jira(query: str, limit: int = 5) -> str:
    """Free-text JQL search via the current /search/jql endpoint.

    The legacy /rest/api/3/search was removed by Atlassian (HTTP 410); this uses
    the replacement, which paginates with nextPageToken (we only take page one).
    """
    base = os.environ["JIRA_BASE_URL"].rstrip("/")
    jql = f'text ~ "{_quote_escape(query)}" ORDER BY updated DESC'
    r = requests.post(
        f"{base}/rest/api/3/search/jql",
        json={"jql": jql, "maxResults": limit,
              "fields": ["summary", "status", "resolution", "updated"]},
        auth=_atlassian_auth(),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        timeout=15,
    )
    r.raise_for_status()
    hits = []
    for issue in r.json().get("issues", []):
        f = issue.get("fields", {})
        hits.append({
            "key": issue.get("key"),
            "summary": f.get("summary"),
            "status": (f.get("status") or {}).get("name"),
            "resolution": (f.get("resolution") or {}).get("name") or "Unresolved",
            "url": f"{base}/browse/{issue.get('key')}",
        })
    return json.dumps(hits, indent=2) if hits else "No matching Jira issues."


def create_jira_issue(summary: str, description: str):
    if _dry():
        key = "DRY-" + uuid.uuid4().hex[:4].upper()
        log_event("TICKET", f"[dry-run] would open Jira: {summary[:120]} → {key}", ticket=key)
        return key, f"https://dry-run.local/browse/{key}"
    base = os.environ["JIRA_BASE_URL"].rstrip("/")
    payload = {"fields": {
        "project": {"key": os.environ["JIRA_PROJECT_KEY"]},
        "issuetype": {"name": os.environ.get("JIRA_ISSUE_TYPE", "Task")},
        "summary": summary[:250],
        "description": _adf_text(description),
    }}
    r = requests.post(f"{base}/rest/api/3/issue", json=payload, auth=_atlassian_auth(),
                      headers={"Accept": "application/json", "Content-Type": "application/json"},
                      timeout=20)
    r.raise_for_status()
    key = r.json()["key"]
    log_event("TICKET", f"opened Jira {key}: {summary[:120]}", ticket=key)
    return key, f"{base}/browse/{key}"


def add_jira_comment(key: str, body_doc: dict):
    if _dry():
        log_event("TICKET", f"[dry-run] would comment on {key}")
        return
    base = os.environ["JIRA_BASE_URL"].rstrip("/")
    r = requests.post(f"{base}/rest/api/3/issue/{key}/comment", json={"body": body_doc},
                      auth=_atlassian_auth(),
                      headers={"Accept": "application/json", "Content-Type": "application/json"},
                      timeout=20)
    r.raise_for_status()


def _jira_transition(key: str, transition_id: str):
    base = os.environ["JIRA_BASE_URL"].rstrip("/")
    requests.post(f"{base}/rest/api/3/issue/{key}/transitions",
                  json={"transition": {"id": str(transition_id)}}, auth=_atlassian_auth(),
                  headers={"Accept": "application/json", "Content-Type": "application/json"},
                  timeout=15).raise_for_status()


def _jira_close(key: str):
    """Move an issue to its done state. Prefers an explicit JIRA_DONE_TRANSITION_ID
    if set; otherwise looks the transition up by target status name
    (JIRA_DONE_STATUS, default 'Done') so no workflow-specific id is needed."""
    if _dry():
        log_event("TICKET", f"[dry-run] would close {key}")
        return
    tid = os.environ.get("JIRA_DONE_TRANSITION_ID")
    if not tid:
        base = os.environ["JIRA_BASE_URL"].rstrip("/")
        want = os.environ.get("JIRA_DONE_STATUS", "Done").lower()
        r = requests.get(f"{base}/rest/api/3/issue/{key}/transitions",
                         auth=_atlassian_auth(), headers={"Accept": "application/json"},
                         timeout=15)
        r.raise_for_status()
        for t in r.json().get("transitions", []):
            if want in (t.get("name", "").lower(), (t.get("to") or {}).get("name", "").lower()):
                tid = t["id"]
                break
        if not tid:
            raise RuntimeError(f"no transition to '{want}' available for {key}")
    _jira_transition(key, tid)
