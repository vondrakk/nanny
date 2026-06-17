"""
nanny.py — supervised auto-triage bot for PagerDuty alerts in Slack.

Flow:
  A PagerDuty alert lands in the watched Slack channel
    -> bot reacts (:eyes:), opens a thread, and ACKs the PD incident
    -> agentic loop: search Confluence (runbooks) + Jira (past incidents),
       fetch logs from hosts (READ-ONLY, allowlisted), reason about cause
    -> posts a triage summary back to the thread
  Any *remediation* stays gated behind a human (see the Approve/Reject TODO).

Design rules baked in here:
  - The model NEVER gets a raw shell. It picks (host, path, pattern) from an
    allowlist; this script builds the command. No arbitrary command execution.
  - Every tool call is written to an audit log (SOC 2 / ISO evidence).
  - The loop is turn-capped so a noisy alert can't run forever.

Modes:
  python nanny.py run
      # watch Slack and triage alerts
  python nanny.py console
      # plain command interface to nanny's operations (no LLM) — `help` lists them
  python nanny.py shell
      # LLM reasoning REPL (talk to the triage agent)
  python nanny.py tui
      # live console: dashboard / fleet / incidents / reports (keys d f i r, q quits)
  python nanny.py discover-logs --host web-01 --admin-user ubuntu
      # preview the log files nanny would monitor on a host
  python nanny.py install --host web-01 --admin-user ubuntu --pubkey bot.pub
      # discover logs + provision the read-only account (--logs to set manually)
  python nanny.py uninstall --host web-01 --admin-user ubuntu
      # remove that account when decommissioning
  python nanny.py discover --cidr 10.0.1.0/24
      # inventory services on your network and generate a monitor for each
  python nanny.py monitor
      # check every generated monitor, with retry backoff + PagerDuty escalation
  python nanny.py selftest
      # probe every integration (Anthropic, Slack, Confluence, Jira, PD, SSH)
  python nanny.py server
      # always-on backend: Alertmanager webhook + operator API + shared state
  python nanny.py panel
      # operator control panel: shared incidents, online operators, claim/ack
  python nanny.py report --since 8h
      # shift report from the incident log (counts, MTTR, paged vs self-resolved)

Deps:  pip install anthropic slack-bolt requests
Env (run mode only):
  ANTHROPIC_API_KEY, SLACK_BOT_TOKEN, SLACK_APP_TOKEN, PAGERDUTY_SLACK_BOT_ID
  ATLASSIAN_EMAIL, ATLASSIAN_API_TOKEN      # shared Confluence + Jira (Cloud) auth
  JIRA_BASE_URL          e.g. https://yourco.atlassian.net
  CONFLUENCE_BASE_URL    e.g. https://yourco.atlassian.net/wiki
  PAGERDUTY_API_TOKEN, PAGERDUTY_FROM_EMAIL # From = a real PD user (required to ack)
  PAGERDUTY_API_BASE     optional; default https://api.pagerduty.com (EU: api.eu...)
  PAGERDUTY_ROUTING_KEY  Events API v2 key — used by `monitor` to trigger/resolve
  SLACK_ALERT_CHANNEL    optional; channel id for `monitor` warn/page/recovery posts
"""

import os
import re
import sys
import json
import time
import uuid
import hmac
import shlex
import socket
import hashlib
import secrets
import argparse
import threading
import ipaddress
import subprocess
from collections import deque
from urllib.parse import unquote, quote
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

import anthropic
import requests
from slack_bolt import App
from slack_sdk import WebClient
from slack_bolt.adapter.socket_mode import SocketModeHandler

from nanny.config import *   # constants + env/config + auth/LLM mode
from nanny.util import *     # pure helpers
from nanny.log import *
from nanny.atlassian import *
from nanny.pagerduty import *
from nanny.slack import *
from nanny.ntfy import *
from nanny.icinga import *
from nanny.hostlogs import *
from nanny.store import make_store

# Name of the unprivileged, read-only service account created by `install`.

# ── project / author (edit to taste — surfaced in the `tui` about view) ───────

# ── config ──────────────────────────────────────────────────────────────────
# Slack bot/app id that PagerDuty posts as — we only triage messages from it.

# Seed hosts/paths here if you want, but `install` discovers logs on each host
# and records them, so you don't have to maintain this by hand.



anthropic_client = None   # constructed in run()/shell(); install mode doesn't need it



# ── tools exposed to the model ───────────────────────────────────────────────
TOOLS = [
    {
        "name": "search_confluence",
        "description": "Search Confluence for runbooks/docs relevant to an alert. "
                       "Returns page titles, URLs, and short excerpts.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "search_jira",
        "description": "Search Jira for past incidents/tickets matching a query. "
                       "Returns issue keys, summaries, status, and resolution notes.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "fetch_logs",
        "description": "Read recent log lines from an allowlisted host/file. READ-ONLY. "
                       "Optionally grep for a pattern. Cannot run arbitrary commands.",
        "input_schema": {
            "type": "object",
            "properties": {
                "host":    {"type": "string", "description": "Host name; must be allowlisted."},
                "path":    {"type": "string", "description": "Absolute log path; allowlisted for that host."},
                "pattern": {"type": "string", "description": "Optional grep pattern."},
                "lines":   {"type": "integer", "description": f"Trailing lines (max {MAX_LOG_LINES}).", "default": 200},
            },
            "required": ["host", "path"],
        },
    },
    {
        "name": "get_icinga_state",
        "description": "Get LIVE monitoring state for a host from Icinga: the host's "
                       "up/down state and every service check on it (OK/WARNING/"
                       "CRITICAL/UNKNOWN) with plugin output, plus the runbook link "
                       "(notes_url) if one is set. Use this to see whether the whole "
                       "host is unhealthy or just one check, and to find the runbook. "
                       "READ-ONLY.",
        "input_schema": {
            "type": "object",
            "properties": {
                "host":    {"type": "string", "description": "Host name or address (the alert's instance)."},
                "service": {"type": "string", "description": "Optional: focus the runbook lookup on this service."},
            },
            "required": ["host"],
        },
    },
]

# ── tool implementations ──────────────────────────────────────────────────────










# ── Jira write path (ticket creation / comments) ──────────────────────────────














TOOL_FNS = {
    "search_confluence": lambda i: search_confluence(i["query"]),
    "search_jira":       lambda i: search_jira(i["query"]),
    "fetch_logs":        lambda i: fetch_logs(i["host"], i["path"], i.get("pattern"), i.get("lines", 200)),
    "get_icinga_state":  lambda i: _tool_icinga_state(i.get("host", ""), i.get("service")),
}

SYSTEM = (
    "You are an on-call triage assistant. Given a PagerDuty/Icinga alert, use the "
    "tools to: check live monitoring state for the host (get_icinga_state — is the "
    "whole host down or just this one check? are sibling services also failing?), "
    "find the relevant runbook (a runbook link may already be supplied below; "
    "otherwise search_confluence), check Jira for prior incidents, and inspect logs. "
    "Form a hypothesis about root cause and recommend next steps.\n\n"
    "You are READ-ONLY. Never claim you will take remediating action — instead state "
    "clearly what a human should do.\n\n"
    "End with a concise summary in this shape:\n"
    "  • What fired\n  • What you found\n  • Likely cause\n  • Recommended action\n  • Confidence (low/med/high)"
)

# ── the agentic loop ──────────────────────────────────────────────────────────






def _agent_loop(messages: list, system: str, verbose: bool = False,
                audit: list = None, max_turns: int = MAX_TURNS) -> str:
    """Drive the tool-use loop. Dispatches to the configured backend: the
    Anthropic API (cloud) or a local OpenAI-compatible server (fully offline)."""
    if _llm_backend() in ("local", "openai", "ollama", "vllm"):
        return _agent_loop_local(messages, system, verbose, audit, max_turns)
    return _agent_loop_anthropic(messages, system, verbose, audit, max_turns)


def _record_tool(audit, verbose, name, args, output):
    if verbose:
        print(_c(f"  → {name}({_fmt_args(args)})", "2"))
    log_audit(name, args, output)
    if audit is not None:
        audit.append({"ts": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                      "tool": name, "args": _fmt_args(args),
                      "result": str(output)[:300].replace("\n", " ")})


def _agent_loop_anthropic(messages, system, verbose, audit, max_turns):
    for _ in range(max_turns):
        resp = anthropic_client.messages.create(
            model=MODEL, max_tokens=4000, system=system, tools=TOOLS, messages=messages,
        )
        messages.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason != "tool_use":
            return "".join(b.text for b in resp.content if b.type == "text").strip()
        results = []
        for block in resp.content:
            if block.type == "text" and verbose and block.text.strip():
                print(block.text.strip())
            if block.type != "tool_use":
                continue
            try:
                output = TOOL_FNS[block.name](block.input)
            except Exception as e:
                output = f"ERROR: {e}"
            _record_tool(audit, verbose, block.name, block.input, output)
            results.append({"type": "tool_result", "tool_use_id": block.id,
                            "content": str(output)[:8000]})
        messages.append({"role": "user", "content": results})
    return "Hit the turn limit without concluding."


def _openai_tools():
    return [{"type": "function", "function": {
        "name": t["name"], "description": t["description"], "parameters": t["input_schema"]}}
        for t in TOOLS]


def _agent_loop_local(messages, system, verbose, audit, max_turns):
    """Offline path: talks to a local OpenAI-compatible server (Ollama, vLLM,
    llama.cpp, LM Studio). No data leaves the network."""
    base = os.environ.get("NANNY_LLM_BASE_URL", "http://localhost:11434/v1").rstrip("/")
    model = os.environ.get("NANNY_LLM_MODEL", "llama3.1")
    key = os.environ.get("NANNY_LLM_API_KEY", "local")
    tools = _openai_tools()
    for _ in range(max_turns):
        r = requests.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": model, "max_tokens": 4000, "tools": tools, "tool_choice": "auto",
                  "messages": [{"role": "system", "content": system}] + messages},
            timeout=180)
        r.raise_for_status()
        msg = r.json()["choices"][0]["message"]
        assistant = {"role": "assistant", "content": msg.get("content", "")}
        if msg.get("tool_calls"):
            assistant["tool_calls"] = msg["tool_calls"]
        messages.append(assistant)
        if verbose and (assistant["content"] or "").strip():
            print(assistant["content"].strip())
        calls = msg.get("tool_calls") or []
        if not calls:
            return (assistant["content"] or "").strip()
        for tc in calls:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                args = {}
            try:
                output = TOOL_FNS[name](args)
            except Exception as e:
                output = f"ERROR: {e}"
            _record_tool(audit, verbose, name, args, output)
            messages.append({"role": "tool", "tool_call_id": tc.get("id", ""),
                             "content": str(output)[:8000]})
    return "Hit the turn limit without concluding."


def triage(alert_text: str) -> str:
    messages = [{"role": "user", "content": f"Alert:\n{alert_text}"}]
    return _agent_loop(messages, SYSTEM)




# ── PagerDuty ─────────────────────────────────────────────────────────────────
# PagerDuty incident links look like https://<sub>.pagerduty.com/incidents/PXXXXXX




# ── Slack wiring ──────────────────────────────────────────────────────────────
def on_message(event, client, say):
    # Only handle messages posted by PagerDuty's bot; ignore human chatter.
    if ALERT_BOT_ID and event.get("bot_id") != ALERT_BOT_ID:
        return
    if not event.get("bot_id"):
        return

    alert_text = event.get("text", "")
    ts = event["ts"]
    channel = event["channel"]

    # 1. Ack fast so humans + PagerDuty know it's owned.
    client.reactions_add(channel=channel, timestamp=ts, name="eyes")
    acknowledge_pagerduty(alert_text)
    say(channel=channel, thread_ts=ts, text="On it — investigating. :mag:")

    # 2. Investigate (slow) and post findings in-thread.
    summary = triage(alert_text)
    say(channel=channel, thread_ts=ts, text=summary)

    # 3. (Optional) remediation gate: post Approve / Reject buttons here and run
    #    the fix only after a human clicks Approve. Keeps "nanny" supervised.


# ── run mode ──────────────────────────────────────────────────────────────────
def run():
    """Watch the Slack channel and triage alerts."""
    global anthropic_client
    if _llm_backend() == "anthropic":
        anthropic_client = anthropic.Anthropic()      # reads ANTHROPIC_API_KEY
    app = App(token=os.environ["SLACK_BOT_TOKEN"])
    app.event("message")(on_message)
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()


# ── shell mode ────────────────────────────────────────────────────────────────
SHELL_SYSTEM = (
    "You are nanny, an interactive on-call assistant running in an engineer's "
    "terminal. Help them investigate production issues. You can search Confluence "
    "runbooks, search Jira for prior incidents, and read allowlisted server logs — "
    "use these proactively whenever they'd help rather than asking permission. "
    "You are READ-ONLY: recommend actions for the human to take; never claim to "
    "change anything. If handed an alert, run the full investigation and summarize. "
    "Be concise and practical, like a sharp teammate sitting at the terminal."
)

SHELL_HELP = """commands:
  /help     show this
  /reset    clear the conversation context
  /exit     quit  (Ctrl-D also works)
anything else is sent to the assistant. Paste an alert to triage it, or just ask
a question like "is web-01 throwing 5xx?" or "find the runbook for disk pressure".
"""


def shell():
    """Interactive, tool-using console — a local way to drive the same agent."""
    global anthropic_client
    if _llm_backend() == "anthropic":
        anthropic_client = anthropic.Anthropic()
    backend = MODEL if _llm_backend() == "anthropic" else \
        f"local:{os.environ.get('NANNY_LLM_MODEL', 'llama3.1')}"
    print(_c("nanny", "1;36") + _c(f"  ·  interactive on-call assistant  ·  {backend}", "2"))
    print(_c("type /help for commands, /exit to quit\n", "2"))

    messages: list = []
    while True:
        try:
            line = input(_c("nanny> ", "1;36")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line in ("/exit", "/quit"):
            break
        if line == "/reset":
            messages.clear()
            print(_c("(context cleared)\n", "2"))
            continue
        if line == "/help":
            print(SHELL_HELP)
            continue

        messages.append({"role": "user", "content": line})
        try:
            answer = _agent_loop(messages, SHELL_SYSTEM, verbose=True)
        except KeyboardInterrupt:
            print(_c("\n(interrupted)\n", "2"))
            continue
        except Exception as e:
            print(_c(f"error: {e}\n", "31"))
            continue
        print("\n" + answer + "\n")


# ── install mode ──────────────────────────────────────────────────────────────
# Host-side log reader. This is the SECOND, independent enforcement layer: even
# if the bot is compromised or its key leaks, this wrapper is all the account
# can run, and it only reads files baked into ALLOWED below. No shell is ever
# invoked (subprocess uses arg lists), grep uses -F so patterns can't inject.




# ── log discovery + install ───────────────────────────────────────────────────
# Enumerate live log files on a host (run as admin, with sudo so root-only logs
# are visible — `install` then grants the read-only account access to them).










# ── uninstall mode ────────────────────────────────────────────────────────────




# ── discover + monitor ────────────────────────────────────────────────────────
# Network discovery here is ASSET INVENTORY for infrastructure you operate. It
# uses plain TCP connect scans (no raw sockets, no exploitation, no credentials)
# — the same thing `nmap -sT` does — to find which hosts are up and which service
# ports are open, then writes one monitor per service. Only point it at ranges
# you are authorized to scan.

# Service ports we probe, and how each should be monitored (tcp connect vs http).
PORT_SERVICES = {
    22: ("ssh", "tcp"),          25: ("smtp", "tcp"),       53: ("dns", "tcp"),
    80: ("http", "http"),        443: ("https", "https"),   3000: ("grafana", "http"),
    3306: ("mysql", "tcp"),      5432: ("postgres", "tcp"), 5672: ("rabbitmq", "tcp"),
    6379: ("redis", "tcp"),      8000: ("http-alt", "http"),8080: ("http-alt", "http"),
    8443: ("https-alt", "https"),9090: ("prometheus", "http"),
    9100: ("node-exporter", "http"), 9200: ("elasticsearch", "http"),
    27017: ("mongodb", "tcp"),
}
DEFAULT_SCAN_PORTS = sorted(PORT_SERVICES)

# Timing knobs the `monitor` loop uses. Per-monitor overrides win over these.
MONITOR_DEFAULTS = {
    "interval": 60,               # seconds between checks while healthy
    "timeout": 5,                 # per-check timeout
    "retries": 3,                 # quick retries before a failure is "confirmed"
    "retry_backoff_base": 2,      # seconds; doubles each retry (2, 4, 8, …)
    "escalate_after_failures": 3, # confirmed failures in a row before paging
    "reescalate_every": 900,      # seconds; re-page if still down after this long
    "max_check_backoff": 600,     # cap on the slow-poll interval while down
}


def _port_open(ip: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def generate_monitors(inventory: dict) -> list:
    """One monitor per (host, open port). HTTP-ish ports get an HTTP check."""
    mons = []
    for ip, services in sorted(inventory.items()):
        for s in services:
            kind = PORT_SERVICES.get(s["port"], (s["service"], "tcp"))[1]
            m = {"name": f"{ip}:{s['port']} {s['service']}",
                 "host": ip, "port": s["port"], "type": kind}
            if kind in ("http", "https"):
                m["path"] = "/"   # adjust to a real health endpoint per service
            mons.append(m)
    return mons


def generate_prom_targets(inventory: dict) -> list:
    """Blackbox-exporter file_sd targets for the bundled Prometheus stack.

    http/https → an http_2xx probe against the URL; everything else → a
    tcp_connect probe against host:port. The module rides along as a label so a
    single Prometheus scrape job can handle mixed probe types.
    """
    groups = []
    for ip, services in sorted(inventory.items()):
        for s in services:
            kind = PORT_SERVICES.get(s["port"], (s["service"], "tcp"))[1]
            if kind == "http":
                target, module = f"http://{ip}:{s['port']}", "http_2xx"
            elif kind == "https":
                target, module = f"https://{ip}:{s['port']}", "http_2xx"
            else:
                target, module = f"{ip}:{s['port']}", "tcp_connect"
            groups.append({
                "targets": [target],
                "labels": {"__param_module": module, "service": s["service"]},
            })
    return groups


def discover(cidrs: list, ports: list, out_path: str = None, monitors_path: str = None,
             prom_path: str = None, max_workers: int = 100):
    """Scan operator-specified CIDRs, inventory open service ports, emit monitors."""
    # Default outputs to the writable data dir (the container runs non-root, so
    # the working dir isn't writable). Prometheus watches NANNY_TARGETS_DIR.
    out_path = out_path or _data_path("inventory.json")
    monitors_path = monitors_path or _data_path("monitors.json")
    targets_dir = _env_dir("NANNY_TARGETS_DIR", _data_path("targets"))
    prom_path = prom_path or os.path.join(targets_dir, "discovered.json")
    for p in (out_path, monitors_path, prom_path):
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)

    addrs = []
    for c in cidrs:
        addrs.extend(str(h) for h in ipaddress.ip_network(c, strict=False).hosts())

    print(_c(f"discover: scanning {len(addrs)} address(es) × {len(ports)} ports "
             f"across {', '.join(cidrs)}", "1"))
    print(_c("only run this against networks you are authorized to scan.\n", "2"))

    def scan(ip):
        found = [{"port": p, "service": PORT_SERVICES.get(p, (f"port-{p}", "tcp"))[0]}
                 for p in ports if _port_open(ip, p)]
        return ip, found

    inventory = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for ip, found in ex.map(scan, addrs):
            if found:
                inventory[ip] = found
                svc = ", ".join(f"{f['port']}/{f['service']}" for f in found)
                print(_c(f"  {ip}", "1;32") + f"  {svc}")

    with open(out_path, "w") as fh:
        json.dump(inventory, fh, indent=2)
    monitors = generate_monitors(inventory)
    with open(monitors_path, "w") as fh:
        json.dump({"defaults": MONITOR_DEFAULTS, "monitors": monitors}, fh, indent=2)
    with open(prom_path, "w") as fh:
        json.dump(generate_prom_targets(inventory), fh, indent=2)

    print(f"\nfound {len(inventory)} host(s); wrote {out_path}, "
          f"{len(monitors)} monitor(s) to {monitors_path}, and blackbox targets "
          f"to {prom_path}.")
    print(_c("review before running `monitor` (standalone) or dropping the targets "
             "file into the bundled Prometheus stack — set real health paths and "
             "thresholds.", "2"))
    return inventory


# ── icinga as source of truth ─────────────────────────────────────────────────
# When you already run Icinga, it knows what exists and whether it's healthy.
# nanny pulls hosts/services + live state straight from the Icinga2 REST API
# instead of scanning the network — the fleet view reads this, not Prometheus.
















# Attributes pulled for every host/service so the fleet view can show real state,
# the plugin output, notes, ack, freshness and attempts — not just up/down. (We do
# NOT request downtime_depth: it isn't a valid query field on older Icinga, e.g.
# 2.4.x — the in-downtime flag is derived from the /objects/downtimes query instead.)
















def discover_icinga(out_path: str = None, monitors_path: str = None,
                    prom_path: str = None):
    """Build nanny's inventory from the Icinga2 API instead of scanning the network.
    Writes the same inventory.json; with Icinga as the source of truth the fleet
    view reads Icinga live, so the Prometheus/blackbox monitors + targets are now
    emitted only best-effort, for any services that expose a port."""
    out_path = out_path or _data_path("inventory.json")
    monitors_path = monitors_path or _data_path("monitors.json")
    targets_dir = _env_dir("NANNY_TARGETS_DIR", _data_path("targets"))
    prom_path = prom_path or os.path.join(targets_dir, "discovered.json")
    for p in (out_path, monitors_path, prom_path):
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)

    print(_c(f"discover: querying Icinga at {_env_dir('ICINGA_API_URL', '?')}", "1"))
    inv = icinga_inventory()
    svc_total = sum(len(v) for v in inv.values())
    down = sum(1 for v in inv.values() for e in v if not e["up"])
    for addr, svcs in sorted(inv.items()):
        bad = [e["service"] for e in svcs if not e["up"]]
        line = _c(f"  {addr}", "1;31" if bad else "1;32") + f"  {len(svcs)} check(s)"
        if bad:
            line += _c(f"  not-ok: {', '.join(bad)}", "31")
        print(line)

    with open(out_path, "w") as fh:
        json.dump(inv, fh, indent=2)

    # Port-bearing entries can still feed the optional standalone monitor / blackbox
    # substrate; everything else is monitored by Icinga, not re-probed by nanny.
    probe_inv = {addr: [e for e in svcs if e.get("port")] for addr, svcs in inv.items()}
    probe_inv = {a: v for a, v in probe_inv.items() if v}
    monitors = generate_monitors(probe_inv)
    with open(monitors_path, "w") as fh:
        json.dump({"defaults": MONITOR_DEFAULTS, "monitors": monitors}, fh, indent=2)
    with open(prom_path, "w") as fh:
        json.dump(generate_prom_targets(probe_inv), fh, indent=2)

    print(f"\n{len(inv)} host(s), {svc_total} check(s) from Icinga ({down} not OK); "
          f"wrote {out_path}.")
    if monitors:
        print(_c(f"also emitted {len(monitors)} optional blackbox monitor(s) for "
                 f"port-bearing services.", "2"))
    return inv


# ── monitor checks ────────────────────────────────────────────────────────────
def _single_check(m: dict, timeout: float):
    if m["type"] in ("http", "https"):
        scheme = "https" if m["type"] == "https" else "http"
        url = f"{scheme}://{m['host']}:{m['port']}{m.get('path', '/')}"
        try:
            r = requests.get(url, timeout=timeout, verify=False, allow_redirects=True)
            return r.status_code < 500, f"{url} -> {r.status_code}"
        except requests.RequestException as e:
            return False, f"{url} -> {type(e).__name__}"
    ok = _port_open(m["host"], m["port"], timeout)
    return ok, f"tcp {m['host']}:{m['port']} {'open' if ok else 'closed'}"


def _attempt(m: dict, d: dict):
    """Run the check with quick exponential-backoff retries to absorb blips."""
    retries = max(1, m.get("retries", d["retries"]))
    base = m.get("retry_backoff_base", d["retry_backoff_base"])
    timeout = m.get("timeout", d["timeout"])
    detail = ""
    for i in range(retries):
        ok, detail = _single_check(m, timeout)
        if ok:
            return True, detail
        if i < retries - 1:
            time.sleep(min(base * (2 ** i), 30))
    return False, detail


def _interval(m: dict, d: dict) -> int:
    return m.get("interval", d["interval"])


def _apply_result(m: dict, st: dict, d: dict, ok: bool, detail: str):
    now = time.time()
    name = m["name"]
    if ok:
        if st["status"] == "down":
            notify("RECOVERED", f"{name} is back up ({detail})")
            pd_event("resolve", st["dedup"], f"{name} recovered")
        st.update(status="up", fails=0, paged_at=0.0, level=0)
        st["next_due"] = now + _interval(m, d)
        return

    st["fails"] += 1
    st["status"] = "down"
    if st["fails"] == 1:                              # first blip → warn only
        notify("WARN", f"{name} failed a check ({detail})")

    escalate_after = m.get("escalate_after_failures", d["escalate_after_failures"])
    reescalate = m.get("reescalate_every", d["reescalate_every"])
    if st["fails"] >= escalate_after and st["paged_at"] == 0.0:
        notify("PAGE", f"{name} down for {st['fails']} checks — paging ({detail})")
        pd_event("trigger", st["dedup"], f"{name} is DOWN: {detail}", "critical")
        st.update(paged_at=now, level=1)
    elif st["paged_at"] and now - st["paged_at"] >= reescalate:
        st["level"] += 1
        notify("PAGE", f"{name} still down (level {st['level']}) — re-escalating")
        pd_event("trigger", st["dedup"],
                 f"{name} STILL DOWN (level {st['level']}): {detail}", "critical")
        st["paged_at"] = now

    # Back off the polling cadence so we don't hammer a service that's already down.
    iv = min(_interval(m, d) * (2 ** st["fails"]),
             m.get("max_check_backoff", d["max_check_backoff"]))
    st["next_due"] = now + iv


# ── activity log ──────────────────────────────────────────────────────────────
# An in-memory ring buffer of everything nanny does (pages, tickets, triage steps,
# discoveries), surfaced live in the console's "log" tab via /api/log. notify() and
# the action helpers feed it, so the window reflects the agent's real activity.




# ── log forwarding (cloud-native / SIEM) ──────────────────────────────────────
# Cloud-native path: structured JSON to stdout (NANNY_LOG_JSON=true) — OpenShift's
# log stack (Vector/Fluentd → Loki/Elasticsearch) collects pod stdout for free.
# Optional direct fan-out to a syslog collector (NANNY_LOG_SYSLOG=host:port) for a
# SIEM. Best-effort: a logging sink must never crash or block nanny.












def monitor(path: str):
    """Check every monitor on its interval, with retry backoff and escalation."""
    try:
        import urllib3
        urllib3.disable_warnings()      # quiet self-signed-cert warnings on health checks
    except Exception:
        pass
    try:
        cfg = json.load(open(path))
    except OSError as e:
        sys.exit(f"monitor: can't read {path}: {e} — run `discover` first.")

    d = {**MONITOR_DEFAULTS, **(cfg.get("defaults") or {})}
    mons = cfg.get("monitors") or []
    if not mons:
        sys.exit(f"monitor: no monitors defined in {path}.")

    print(_c(f"monitor: watching {len(mons)} service(s) — Ctrl-C to stop\n", "1"))
    state = {m["name"]: {"status": "unknown", "fails": 0, "next_due": 0.0,
                         "paged_at": 0.0, "level": 0, "dedup": f"nanny/{m['name']}"}
             for m in mons}
    try:
        while True:
            now = time.time()
            for m in mons:
                st = state[m["name"]]
                if now < st["next_due"]:
                    continue
                ok, detail = _attempt(m, d)
                _apply_result(m, st, d, ok, detail)
            time.sleep(1)
    except KeyboardInterrupt:
        print(_c("\nmonitor: stopped.\n", "2"))


# ── selftest ──────────────────────────────────────────────────────────────────
def _ssh_probe(host: str, path: str):
    """End-to-end check: network + key + forced-command wrapper + read perms."""
    remote = f"1 {shlex.quote(path)}"   # ask the wrapper for 1 line of an allowlisted file
    p = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
         f"{SVC_USER}@{host}", remote],
        capture_output=True, text=True, timeout=20,
    )
    if p.returncode == 0:
        return True, "reachable, read ok"
    msg = (p.stderr or p.stdout or "").strip().splitlines()
    return False, (msg[-1] if msg else f"exit {p.returncode}")[:120]


# ── integration self-tests (shared by the `selftest` CLI and the web controls) ─
# Each probe is a read-only reachability check — no tickets, no pages — so it's
# safe to run from a button at any time (even with NANNY_DRY_RUN off).
def _chk_llm():
    if not _llm_enabled():
        return (True, "disabled — running in no-LLM mode (NANNY_LLM_BACKEND=none)")
    if _llm_backend() != "anthropic":
        base = os.environ.get("NANNY_LLM_BASE_URL", "http://localhost:11434/v1").rstrip("/")
        r = requests.get(f"{base}/models",
                         headers={"Authorization":
                                  f"Bearer {os.environ.get('NANNY_LLM_API_KEY', 'local')}"},
                         timeout=8)
        return r.ok, f"local {os.environ.get('NANNY_LLM_MODEL', '?')} at {base}"
    r = anthropic.Anthropic().messages.create(
        model=MODEL, max_tokens=1, messages=[{"role": "user", "content": "ping"}])
    return (bool(r.content) or r.stop_reason is not None), f"{MODEL} reachable"


def _chk_slack():
    r = WebClient(token=os.environ["SLACK_BOT_TOKEN"]).auth_test()
    return bool(r.get("ok")), f"team={r.get('team')} bot={r.get('user')}"


def _chk_pagerduty():
    base = os.environ.get("PAGERDUTY_API_BASE", "https://api.pagerduty.com").rstrip("/")
    r = requests.get(
        f"{base}/abilities",
        headers={"Authorization": f"Token token={os.environ['PAGERDUTY_API_TOKEN']}",
                 "Accept": "application/json"}, timeout=10)
    key = " · events routing key set" if os.environ.get("PAGERDUTY_ROUTING_KEY") else ""
    return r.ok, f"token valid (abilities -> {r.status_code}){key}"


def _chk_ntfy():
    base, topic = ntfy_base(), ntfy_topic()
    ok = ntfy_send("INVESTIGATING", "nanny integration test — if you see this, ntfy works.",
                   title="nanny · test")
    extra = " · interactive (buttons enabled)" if ntfy_interactive() else ""
    return ok, (f"sent a test push to {base}/{topic}{extra}" if ok
                else f"publish to {base}/{topic} failed")


def _chk_icinga():
    cib = _icinga_get("status/CIB")
    s = (cib[0].get("status") if cib else {}) or {}
    return True, (f"reachable — {s.get('num_services_ok', '?')} svc OK / "
                  f"{s.get('num_services_critical', '?')} crit, "
                  f"{s.get('num_hosts_up', '?')} hosts up")


def _chk_confluence():
    return (search_confluence("selftest", 1) is not None, "reachable — search ok")


def _chk_jira():
    return (search_jira("selftest", 1) is not None, "reachable — search ok")


def _integrations():
    """Display order; each row is (name, label, probe, required-env). Read fresh so
    `needs` reflects the current environment (the LLM backend can change it)."""
    return [
        ("icinga", "Icinga", _chk_icinga,
         ["ICINGA_API_URL", "ICINGA_API_USER", "ICINGA_API_PASSWORD"]),
        ("slack", "Slack", _chk_slack, ["SLACK_BOT_TOKEN"]),
        ("jira", "Jira", _chk_jira,
         ["JIRA_BASE_URL", "ATLASSIAN_EMAIL", "ATLASSIAN_API_TOKEN"]),
        ("confluence", "Confluence", _chk_confluence,
         ["CONFLUENCE_BASE_URL", "ATLASSIAN_EMAIL", "ATLASSIAN_API_TOKEN"]),
        ("pagerduty", "PagerDuty", _chk_pagerduty, ["PAGERDUTY_API_TOKEN"]),
        ("ntfy", "ntfy", _chk_ntfy, ["NANNY_NTFY_TOPIC"]),
        ("llm", "LLM", _chk_llm,
         [] if _llm_backend() != "anthropic" else ["ANTHROPIC_API_KEY"]),
    ]


def _run_check(name, fn, needs):
    """Run one probe → {name, status: PASS|FAIL|SKIP, detail}. Never raises."""
    missing = [v for v in needs if not os.environ.get(v)]
    if missing:
        return {"name": name, "status": "SKIP",
                "detail": f"not configured ({', '.join(missing)})"}
    try:
        ok, detail = fn()
        return {"name": name, "status": "PASS" if ok else "FAIL", "detail": detail}
    except Exception as e:
        return {"name": name, "status": "FAIL", "detail": f"{type(e).__name__}: {e}"}


def run_integration_check(name: str):
    """Run a single named integration probe for the web controls. None if unknown."""
    for n, label, fn, needs in _integrations():
        if n == name:
            r = _run_check(n, fn, needs)
            r["label"] = label
            return r
    return None


def _integrations_status() -> list:
    """Lightweight (no network) list of integrations + whether each is configured."""
    return [{"name": n, "label": label,
             "configured": all(os.environ.get(v) for v in needs)}
            for n, label, fn, needs in _integrations()]


def selftest():
    """Preflight: confirm each integration is reachable from where we're running.
    Configured-but-broken counts as FAIL; not-configured is SKIP. Exits non-zero
    on any failure, so it's usable as a deploy gate."""
    results = []

    for n, label, fn, needs in _integrations():
        r = _run_check(n, fn, needs)
        results.append((r["name"], r["status"], r["detail"]))

    allow = _log_allowlist()
    if allow:
        for host, paths in allow.items():
            if paths:
                check(f"ssh log read [{host}]", lambda h=host, p=paths[0]: _ssh_probe(h, p))
    else:
        results.append(("ssh log read", "SKIP", "no hosts provisioned yet (run install)"))

    width = max(len(n) for n, _, _ in results)
    colors = {"PASS": "32", "FAIL": "1;31", "SKIP": "2"}
    print()
    for name, status, detail in results:
        print(f"  [{_c(f'{status:4}', colors[status])}] {name.ljust(width)}  {_c(detail, '2')}")
    fails = sum(s == "FAIL" for _, s, _ in results)
    passes = sum(s == "PASS" for _, s, _ in results)
    skips = sum(s == "SKIP" for _, s, _ in results)
    print(_c(f"\n{passes} passed, {fails} failed, {skips} skipped\n",
             "1;31" if fails else "1;32"))
    sys.exit(1 if fails else 0)


# ── incident automation (Alertmanager webhook) ───────────────────────────────
# Alertmanager forwards firing/resolved alerts here. On firing, nanny opens a
# Jira ticket, runs the triage agent, writes the analysis AND a full audit log of
# every action it took onto the ticket, then decides *whether and when* to page:
# alerts whose signature historically self-resolves are held longer before the
# page fires, so on-call isn't woken for things that clear themselves.

HOLD_DEFAULT = 120      # seconds to hold before paging when we have no history
HOLD_MAX = 900          # cap on the adaptive hold
HOLD_MARGIN = 30        # added to the observed self-resolve time
MIN_SAMPLES = 3         # need this many past occurrences before trusting history
SELF_RESOLVE_MIN_RATE = 0.6   # fraction that must self-resolve to extend the hold

# All shared state (incidents, sessions, jobs, history, audit feed) lives behind
# the Store interface so the web/brain stay stateless and scale horizontally:
# MemoryStore for single-node/dev (DATABASE_URL unset), PostgresStore (CNPG) for
# multi-replica. The delayed page is a `page_at` column drained by a reconciler
# every replica runs — see _reconciler_loop / Store.claim_due_pages.
STORE = make_store()
RECONCILE_SECS = float(os.environ.get("NANNY_RECONCILE_SECS", "5"))
ONLINE_TTL = 30         # seconds; operators seen within this window are "online"
SESSION_IDLE_TTL = 30 * 60      # a session dies after this long with no activity
SESSION_ABS_TTL = 12 * 3600     # …and after this long regardless of activity










def record_outcome(sig: str, self_resolved: bool, duration: float):
    """Remember whether an alert signature cleared on its own, and how fast."""
    STORE.history_record(sig, self_resolved, duration)


def compute_hold(sig: str) -> int:
    """How long to wait before paging this signature, learned from history."""
    samples = STORE.history_samples(sig)
    if len(samples) < MIN_SAMPLES:
        return HOLD_DEFAULT
    self_res = [x for x in samples if x["self_resolved"]]
    if not self_res or len(self_res) / len(samples) < SELF_RESOLVE_MIN_RATE:
        return HOLD_DEFAULT
    durs = sorted(x["duration"] for x in self_res)
    p90 = durs[min(len(durs) - 1, int(0.9 * len(durs)))]
    return int(min(p90 + HOLD_MARGIN, HOLD_MAX))


def _log_incident(rec: dict):
    STORE.log_incident(rec)




def triage_for_incident(alert: dict, context: str = ""):
    """Run the agent against an alert; return (description, analysis, audit trail).
    `context` is an optional pre-fetched hint (runbook link + live Icinga state) the
    caller supplies so the agent starts with that grounding instead of rediscovering
    it — it can still call the tools to dig deeper."""
    labels, ann = alert.get("labels", {}), alert.get("annotations", {})
    desc = (f"Alert: {labels.get('alertname')}\nService: {labels.get('service')}\n"
            f"Instance: {labels.get('instance')}\nSeverity: {labels.get('severity')}\n"
            f"Summary: {ann.get('summary')}\nDescription: {ann.get('description')}\n"
            f"Started: {alert.get('startsAt')}")
    prompt = ("A monitoring alert fired. Investigate with your tools and propose "
              "probable cause and concrete resolutions.\n\n" + desc)
    if context:
        prompt += "\n\n--- context already gathered for you ---\n" + context
    audit = []
    analysis = _agent_loop([{"role": "user", "content": prompt}], SYSTEM, audit=audit)
    return desc, analysis, audit


def _format_audit(audit: list) -> str:
    lines = []
    for a in audit:
        lines.append(f"{a['ts']}  {a['tool']}({a['args']})")
        lines.append(f"          -> {a['result']}")
    return "\n".join(lines) or "(no tool calls)"


# Icinga state → severity. Host states (UP/DOWN/UNREACHABLE) and service states
# (OK/WARNING/CRITICAL/UNKNOWN) both map here.
_ICINGA_SEV = {"CRITICAL": "critical", "DOWN": "critical", "UNREACHABLE": "critical",
               "WARNING": "warning", "UNKNOWN": "warning"}


def _alert_from_icinga(p: dict) -> dict:
    """Map an Icinga notification payload into nanny's internal alert dict (the same
    shape the Alertmanager webhook produces), so the existing incident brain drives
    it. Recovery / OK / UP / downtime-start map to a 'resolved' alert."""
    ntype = str(p.get("type") or p.get("notification_type") or "PROBLEM").upper()
    host = p.get("host") or p.get("host_name") or "?"
    service = p.get("service") or p.get("service_name") or ""
    state = str(p.get("state") or "").upper()
    output = p.get("output") or p.get("message") or ""
    addr = p.get("address") or host
    name = service or host
    resolved = ntype in ("RECOVERY", "DOWNTIMESTART") or state in ("OK", "UP")
    sev = _ICINGA_SEV.get(state, "critical")
    summary = (f"{state or ntype} {name}").strip() + (f": {output}" if output else "")
    try:
        hold = int(p.get("hold", 0) or 0)
    except (TypeError, ValueError):
        hold = 0
    return {
        "status": "resolved" if resolved else "firing",
        "fingerprint": f"icinga/{host}/{service}",
        "labels": {"alertname": name, "service": service or "host",
                   "instance": addr, "severity": sev},
        "annotations": {"summary": summary, "description": output},
        "startsAt": datetime.now(timezone.utc).isoformat(),
        "_hold": hold, "_source": "icinga",
    }


def _simulated_triage(alert: dict):
    """Dry-run stand-in for the LLM triage agent: emits realistic triage steps to
    the activity log and returns a canned analysis + audit, so the whole lifecycle
    can be exercised offline with no API calls or real side effects."""
    labels, ann = alert.get("labels", {}), alert.get("annotations", {})
    inst = labels.get("instance", ""); svc = labels.get("service") or labels.get("alertname", "")
    steps = [
        ("search_confluence", f"runbook for {svc}",
         f"found 'Runbook: {svc}' — restart procedure + escalation contacts"),
        ("search_jira", f"prior incidents for {svc}",
         "2 prior tickets; the last one self-resolved in ~4m"),
        ("read_logs", f"{inst}:/var/log (read-only)",
         "tail shows repeated connection timeouts over the last 5m"),
    ]
    audit = []
    for tool, args, result in steps:
        log_event("TRIAGE", f"[dry-run] {tool}({args}) → {result}")
        audit.append({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                      "tool": tool, "args": args, "result": result})
    analysis = (f"[simulated] Probable cause: {svc} on {inst} is failing its check "
                f"({ann.get('summary', '')}). Recommended: follow the runbook restart, then "
                f"verify; prior occurrences self-resolved, so a short hold is reasonable.")
    desc = (f"Alert: {labels.get('alertname')}\nService: {svc}\nInstance: {inst}\n"
            f"Severity: {labels.get('severity')}\nSummary: {ann.get('summary')}")
    return desc, analysis, audit


def _deterministic_triage(alert: dict, context: str = ""):
    """No-LLM triage (NANNY_LLM_BACKEND=none). Gathers the same evidence an LLM agent
    would — live Icinga state, the runbook, prior Jira incidents, self-resolve
    history — by calling the tools DIRECTLY, then assembles a factual (un-reasoned)
    summary. No model, no token cost. Unlike dry-run this is real operation: the
    ticket and page still happen; only the reasoning is skipped."""
    labels, ann = alert.get("labels", {}), alert.get("annotations", {})
    svc = labels.get("service") or labels.get("alertname", "")
    inst = labels.get("instance", "")
    desc = (f"Alert: {labels.get('alertname')}\nService: {svc}\nInstance: {inst}\n"
            f"Severity: {labels.get('severity')}\nSummary: {ann.get('summary')}\n"
            f"Description: {ann.get('description')}\nStarted: {alert.get('startsAt')}")
    audit, parts = [], []

    def step(tool, args, result):
        log_event("TRIAGE", f"[no-llm] {tool}({args}) → {_trunc(result, 80)}")
        audit.append({"ts": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                      "tool": tool, "args": str(args),
                      "result": str(result)[:300].replace("\n", " ")})

    if context:                                       # already-fetched live Icinga state
        parts.append("LIVE STATE (Icinga):\n" + context)
        step("get_icinga_state", inst, "captured live host/service state")
    for tool, fn, label in (("search_confluence", search_confluence, "RUNBOOKS (Confluence)"),
                            ("search_jira", search_jira, "PRIOR INCIDENTS (Jira)")):
        try:
            out = fn(svc, 3)
            parts.append(f"{label}:\n{out}")
            step(tool, svc, _trunc(out, 160))
        except Exception as e:
            step(tool, svc, f"skipped ({e.__class__.__name__})")
    sig = _signature(labels)                          # self-resolve history for this signature
    samples = STORE.history_samples(sig)
    if samples:
        sr = sum(1 for x in samples if x.get("self_resolved"))
        parts.append(f"HISTORY: {sig} — {sr}/{len(samples)} past occurrences self-resolved.")
        step("history", sig, f"{sr}/{len(samples)} self-resolved")
    analysis = ("Automated triage — NO LLM (NANNY_LLM_BACKEND=none). Evidence was gathered "
                "deterministically; no root-cause reasoning was performed.\n\n"
                + ("\n\n".join(parts) if parts else "(no evidence sources configured)")
                + "\n\nNext: follow the runbook above and have a human confirm root cause.")
    return desc, analysis, audit


def handle_firing(alert: dict):
    fp = alert.get("fingerprint") or json.dumps(alert.get("labels", {}), sort_keys=True)
    labels, ann = alert.get("labels", {}), alert.get("annotations", {})
    sig = _signature(labels)
    summary = ann.get("summary") or f"{labels.get('alertname')} on {labels.get('instance')}"
    started = time.time()
    instance, service = labels.get("instance", ""), labels.get("service", "")
    rec = {"fingerprint": fp, "started": started, "summary": summary, "sig": sig,
           "severity": labels.get("severity", "critical"), "instance": instance,
           "service": service, "phase": "triaging", "hold_secs": 0, "page_at": None,
           "paged": False, "owner": None, "owner_name": None, "acked": False,
           "runbook": "", "notes": "", "analysis": "", "audit": [], "desc": ""}
    if not STORE.incident_create(rec):
        return                                        # already handling this one (idempotent)

    # Pull the runbook link + live Icinga state up front so triage starts grounded
    # and the triage view can show "is the whole host down?" + a runbook button.
    rb = {} if _dry() else _icinga_lookup(instance, service)
    runbook, notes = rb.get("notes_url", ""), rb.get("notes", "")
    context = (_format_icinga_state(rb, instance)
               if (rb.get("host") or rb.get("services")) else "")
    STORE.incident_update(fp, runbook=runbook, notes=notes)

    try:
        if _dry():
            desc, analysis, audit = _simulated_triage(alert)
            runbook = runbook or f"https://confluence.example/runbook/{service or 'host'}"
            STORE.incident_update(fp, runbook=runbook)
        elif not _llm_enabled():
            desc, analysis, audit = _deterministic_triage(alert, context)
        else:
            desc, analysis, audit = triage_for_incident(alert, context=context)
        STORE.incident_update(fp, desc=desc, analysis=analysis, audit=audit)
        key, url = create_jira_issue(f"[nanny] {summary}", desc)
        STORE.incident_update(fp, ticket=key)
        intro = (f"nanny automated triage\n\nProbable cause and resolutions:\n{analysis}\n\n"
                 f"--- audit log: every action nanny took on this incident ---")
        add_jira_comment(key, _adf_with_codeblock(intro, _format_audit(audit)))
        notify("INVESTIGATING", f"{summary} — opened {key} ({url})")
    except Exception as e:
        notify("WARN", f"nanny triage/ticket error for {summary}: {e}")
    _push_incident_card(fp)                            # first interactive card (Claim/Ack)

    # Adaptive page hold — stateless: rather than block a thread on the hold, record
    # WHEN this should page (page_at) and move to the holding phase. The reconciler
    # (every replica) drains due pages with FOR UPDATE SKIP LOCKED, so it fires once.
    # If the alert resolves or an operator claims/acks before page_at, it never pages.
    hold = int(alert.get("_hold") or 0) or compute_hold(sig)
    STORE.incident_update(fp, hold_secs=hold, page_at=started + hold, phase="holding")
    log_event("HOLD", f"{summary} — holding {hold}s before paging (signature {sig})")


def handle_resolved(alert: dict):
    fp = alert.get("fingerprint") or json.dumps(alert.get("labels", {}), sort_keys=True)
    inc = STORE.incident_resolve(fp)                  # atomic firing→resolved; prior record or None
    if not inc:
        return                                        # unknown or already resolved
    labels = alert.get("labels", {})
    sig = _signature(labels)
    duration = time.time() - (inc.get("started") or time.time())
    paged = bool(inc.get("paged"))
    self_resolved = not paged
    record_outcome(sig, self_resolved, duration)

    key = inc.get("ticket")
    closed = False
    if key:
        # Auto-close tickets that cleared on their own; leave paged incidents open
        # for the responder to review and close.
        if self_resolved and os.environ.get("NANNY_AUTOCLOSE", "true").lower() != "false":
            try:
                _jira_close(key)
                closed = True
            except Exception as e:
                notify("WARN", f"jira auto-close failed for {key}: {e}")
        note = (f"Auto-resolved after {int(duration)}s. " +
                ("Self-resolved before paging." if self_resolved else "Recovered after paging.") +
                (" Ticket closed automatically." if closed else ""))
        try:
            add_jira_comment(key, _adf_text(note))
        except Exception as e:
            notify("WARN", f"jira comment failed for {key}: {e}")
    if paged:
        pd_event("resolve", fp, f"{sig} recovered")

    _log_incident({"ended": datetime.now(timezone.utc).isoformat(), "sig": sig,
                   "alertname": labels.get("alertname"), "service": labels.get("service"),
                   "instance": labels.get("instance"), "ticket": key,
                   "paged": paged, "self_resolved": self_resolved,
                   "closed": closed, "duration": round(duration)})
    notify("RECOVERED", f"{sig} resolved in {int(duration)}s" +
           (f" ({key}{' closed' if closed else ''})" if key else ""))
    if cards_enabled() and inc.get("slack_ts"):       # finalize the card (buttons drop off)
        try:
            post_incident({"id": fp, "summary": inc.get("summary"), "sig": sig,
                           "severity": inc.get("severity"), "instance": inc.get("instance"),
                           "service": inc.get("service"), "key": key,
                           "owner_name": inc.get("owner_name"), "acked": inc.get("acked"),
                           "paged": paged, "phase": "resolved",
                           "runbook": inc.get("runbook", ""), "slack_ts": inc.get("slack_ts")})
        except Exception as e:
            log_event("SLACK", f"resolved card update failed for {fp}: {e}")


def _page_incident(inc: dict):
    """Run the page side-effects for one incident the reconciler claimed as due.
    claim_due_pages() has already flipped paged=true / phase=paged atomically, so
    this fires exactly once across all replicas even though every replica reconciles."""
    fp = inc.get("fingerprint")
    summary = inc.get("summary", "")
    sig = inc.get("sig", "")
    hold = inc.get("hold_secs", 0)
    pd_event("trigger", fp, f"{summary} (still firing after {hold}s hold)",
             inc.get("severity", "critical"))
    key = inc.get("ticket")
    if key:
        try:
            add_jira_comment(key, _adf_text(
                f"Paged on-call: still firing after a {hold}s hold "
                f"(history did not show reliable self-resolution for {sig})."))
        except Exception:
            pass
    notify("PAGE", f"{summary} — paged after a {hold}s hold")
    # The page is the moment ntfy matters most: send it here (not via notify) so it can
    # carry Ack/Claim buttons that call back into nanny from the subscriber's device.
    ntfy_send("PAGE", f"{summary}\nStill firing after a {hold}s hold — on-call paged.",
              title=f"🚨 PAGE · {inc.get('instance') or sig}",
              actions=ntfy_incident_actions(fp, owned=bool(inc.get("owner")),
                                            acked=bool(inc.get("acked"))))
    _push_incident_card(fp)


def _reconcile_once() -> int:
    """Drain incidents whose hold has elapsed and page them. Returns how many paged."""
    due = STORE.claim_due_pages()
    for inc in due:
        try:
            _page_incident(inc)
        except Exception as e:
            notify("WARN", f"paging error for {inc.get('fingerprint')}: {e}")
    return len(due)


def _reconciler_loop(interval: float = None):
    """Every replica runs this: poll for due pages and fire them. The atomic claim in
    the store (FOR UPDATE SKIP LOCKED on Postgres) makes paging exactly-once with no
    leader election. Replaces the per-incident in-memory hold thread."""
    interval = interval or RECONCILE_SECS
    while True:
        try:
            _reconcile_once()
        except Exception as e:
            log_event("WARN", f"reconciler error: {e}")
        time.sleep(interval)


def _dispatch_alert(alert: dict):
    try:
        if alert.get("status") == "resolved":
            handle_resolved(alert)
        else:
            handle_firing(alert)
    except Exception as e:
        notify("WARN", f"alert handling error: {e}")


# ── authentication ────────────────────────────────────────────────────────────
# Auth is ALWAYS required to obtain a session token; the mode decides what
# credential proves identity. "open" (name-only, no password) is only permitted
# when the operator explicitly opts in with NANNY_AUTH_INSECURE=true — so the
# secure-by-default posture no longer hinges on whether LDAP happens to be set.






def _login_check(mode: str, name: str, password: str):
    """Validate a login per the active mode. Returns (ok, display_name, err). The
    sentinel err 'not-configured' means open mode without the explicit opt-in (a
    config problem, not a bad credential — the caller must NOT count it as a brute
    force attempt)."""
    if mode == "ldap":
        return _ldap_authenticate(name, password)
    if mode == "password":
        if not name:
            return (False, None, "name is required")
        if not hmac.compare_digest(str(password or ""), os.environ.get("NANNY_AUTH_PASSWORD", "")):
            return (False, None, "invalid console password")
        return (True, name, None)
    # open mode
    if not _auth_open_allowed():
        return (False, None, "not-configured")
    return (True, name or "operator", None)


# ── login rate limiting (F5) ──────────────────────────────────────────────────
LOGIN_MAX_FAILS = 5             # failed logins from one IP within the window…
LOGIN_WINDOW = 300             # …(seconds) before that IP is locked out for the window
_login_fails = {}              # ip -> [recent failure timestamps]
_login_lock = threading.Lock()


def _login_blocked(ip: str) -> bool:
    now = time.time()
    with _login_lock:
        fails = [t for t in _login_fails.get(ip, []) if now - t < LOGIN_WINDOW]
        if fails:
            _login_fails[ip] = fails
        else:
            _login_fails.pop(ip, None)
        return len(fails) >= LOGIN_MAX_FAILS


def _login_record_fail(ip: str):
    with _login_lock:
        _login_fails.setdefault(ip, []).append(time.time())


def _login_clear(ip: str):
    with _login_lock:
        _login_fails.pop(ip, None)


# ── webhook authentication (F4) ───────────────────────────────────────────────
def _webhook_secret() -> str:
    return (os.environ.get("NANNY_WEBHOOK_TOKEN") or "").strip()


def _webhook_authorized(headers, raw: bytes) -> bool:
    """Authorize an inbound /alert or /icinga POST. If NANNY_WEBHOOK_TOKEN is unset,
    webhooks are open (a loud startup warning is printed). When set, accept either an
    HMAC-SHA256 signature of the raw body (X-Nanny-Signature: sha256=...) or a shared
    token in the Authorization: Bearer / X-Nanny-Token header — all constant-time."""
    secret = _webhook_secret()
    if not secret:
        return True
    sig = headers.get("X-Nanny-Signature", "")
    if sig.startswith("sha256="):
        want = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig[7:].strip(), want)
    auth = headers.get("Authorization", "")
    tok = auth[7:].strip() if auth.startswith("Bearer ") else headers.get("X-Nanny-Token", "").strip()
    return bool(tok) and hmac.compare_digest(tok, secret)




def _ldap_authenticate(username: str, password: str):
    """Bind to LDAP as the user to verify credentials. Returns
    (ok: bool, display_name: str|None, error: str|None). Optionally enforces
    group membership (NANNY_LDAP_GROUP) and resolves a friendly display name."""
    username = (username or "").strip()
    if not username or not password:
        return (False, None, "username and password are required")
    try:
        import ldap3
    except ImportError:
        return (False, None, "ldap3 is not installed on the nanny host")
    url = os.environ.get("NANNY_LDAP_URL", "")
    tmpl = os.environ.get("NANNY_LDAP_BIND_DN_TEMPLATE", "")
    domain = os.environ.get("NANNY_LDAP_DOMAIN", "")
    if tmpl:                                          # e.g. uid={user},ou=people,dc=corp
        bind_dn = tmpl.replace("{user}", username).replace("{username}", username)
    elif domain:                                      # Active Directory UPN: user@corp
        bind_dn = f"{username}@{domain}"
    else:
        bind_dn = username
    insecure = _env_flag("NANNY_LDAP_INSECURE")
    use_ssl = url.lower().startswith("ldaps")
    starttls = _env_flag("NANNY_LDAP_STARTTLS")
    tls = None
    if use_ssl or starttls:
        import ssl as _ssl
        tkw = {"validate": _ssl.CERT_NONE if insecure else _ssl.CERT_REQUIRED}
        if _env_flag("NANNY_LDAP_LEGACY_TLS"):        # old directory cert/cipher (cf. ICINGA_LEGACY_TLS)
            tkw["version"] = _ssl.PROTOCOL_TLS_CLIENT
            tkw["ciphers"] = "DEFAULT@SECLEVEL=0"
        tls = ldap3.Tls(**tkw)
    try:
        retries = max(0, int(os.environ.get("NANNY_LDAP_RETRIES", "2") or 2))
    except ValueError:
        retries = 2
    # Retry the CONNECT only (transient socket failures — e.g. the flaky rootless
    # container→VPN path). A reachable server that rejects the password returns
    # False from bind(), which is a real auth failure and is NOT retried.
    conn, bound, last = None, False, None
    for attempt in range(retries + 1):
        try:
            server = ldap3.Server(url, use_ssl=use_ssl, tls=tls, get_info=ldap3.NONE,
                                  connect_timeout=8)
            conn = ldap3.Connection(server, user=bind_dn, password=password, auto_bind=False)
            if starttls and not use_ssl:
                conn.open()
                conn.start_tls()
            bound = conn.bind()
            break                                     # reached the server (bound True/False)
        except Exception as e:
            last = e
            conn = None
            if attempt < retries:
                time.sleep(0.6 * (attempt + 1))
    if conn is None:
        return (False, None, f"ldap unreachable ({last.__class__.__name__ if last else 'error'})")
    try:
        if not bound:                                 # wrong password / unknown user
            conn.unbind()
            return (False, None, "invalid credentials")
        display = username
        base = os.environ.get("NANNY_LDAP_BASE_DN", "")
        group = os.environ.get("NANNY_LDAP_GROUP", "")
        attr = os.environ.get("NANNY_LDAP_LOGIN_ATTR", "sAMAccountName")
        if base:                                      # look up display name + groups
            try:
                conn.search(base, f"({attr}={_ldap_esc(username)})",
                            attributes=["displayName", "cn", "memberOf"])
                if conn.entries:
                    ent = conn.entries[0]
                    for a in ("displayName", "cn"):
                        v = str(getattr(ent, a, "") or "")
                        if v:
                            display = v
                            break
                    if group:
                        member = [str(g) for g in (ent.memberOf.values
                                                   if "memberOf" in ent else [])]
                        if not any(group.lower() in g.lower() for g in member):
                            conn.unbind()
                            return (False, None, "not a member of the required group")
                elif group:                           # can't verify membership → deny
                    conn.unbind()
                    return (False, None, "account not found in directory")
            except Exception:
                pass                                  # bind already proved identity
        conn.unbind()
        return (True, display, None)
    except Exception as e:
        return (False, None, f"ldap unreachable ({e.__class__.__name__})")


# ── operator presence + idempotent claim/ack ──────────────────────────────────
def _op_login(name: str) -> str:
    """Mint a session. The token is 256 bits of CSPRNG entropy (secrets), not a
    truncated uuid — it's the bearer credential, sent in the Authorization header
    (never the URL) and expired by both idle and absolute TTL."""
    token = secrets.token_urlsafe(32)
    STORE.session_create(token, name)
    return token


def _valid_session(token: str):
    """Return the live session for a token, or None if absent/expired. Expiry (idle
    + absolute TTL) is enforced in the store, which also prunes the dead row."""
    return STORE.session_get(token, SESSION_IDLE_TTL, SESSION_ABS_TTL)


def _op_touch(token: str):
    STORE.session_touch(token, SESSION_IDLE_TTL, SESSION_ABS_TTL)


def _op_logout(token: str):
    STORE.session_delete(token)


def _online_operators() -> list:
    return STORE.sessions_online(ONLINE_TTL)


def _op_name(op_id: str) -> str:
    """Display name for an operator/session id, or the id itself if unknown/expired."""
    s = STORE.session_get(op_id, SESSION_IDLE_TTL, SESSION_ABS_TTL)
    return (s or {}).get("name", op_id)


def _inc_view(i: dict) -> dict:
    """Shape a stored incident record into the console's incident summary. Tolerates
    both store backends' field names (ticket/desc vs descr; epoch vs precomputed age)."""
    age = i.get("age")
    if age is None and isinstance(i.get("started"), (int, float)):
        age = int(time.time() - i["started"])
    return {"id": i.get("fingerprint"), "summary": i.get("summary"), "sig": i.get("sig"),
            "severity": i.get("severity"), "instance": i.get("instance"),
            "service": i.get("service"), "ticket": i.get("ticket"),
            "owner": i.get("owner_name"), "acked": i.get("acked"), "paged": i.get("paged"),
            "phase": i.get("phase", "firing"), "hold": i.get("hold_secs", 0),
            "runbook": i.get("runbook", ""), "age": age or 0}


def _incidents_snapshot() -> list:
    return [_inc_view(i) for i in STORE.incidents_firing()]


def _busy_count() -> int:
    """How much background work nanny is doing right now: incidents still being
    triaged or holding before a page-decision, plus any running on-demand job.
    Drives the console's activity spinner."""
    return STORE.busy_count()


def _incident_detail(fp: str) -> dict:
    """Full record for one incident for the triage view: triage analysis + audit
    trail, runbook, hold/phase, ticket, owner — plus FRESH live Icinga state for the
    host (so the operator sees current reality, not triage-time state)."""
    i = STORE.incident_get(fp)
    if not i or i.get("status") != "firing":
        return None
    d = _inc_view(i)
    d.update({"notes": i.get("notes", ""), "analysis": i.get("analysis", ""),
              "audit": i.get("audit") or [], "desc": i.get("desc") or i.get("descr", ""),
              "held_for": d["age"]})
    try:                                                 # fresh state for the operator
        d["state"] = _icinga_lookup(d["instance"], d["service"])
    except Exception:
        d["state"] = {}
    return d


def _reports_payload() -> dict:
    wins = {"8h": 8 * 3600, "24h": 86400, "7d": 7 * 86400}
    return {"windows": {k: _summarize(_read_incidents(v)) for k, v in wins.items()},
            "recent": sorted(_read_incidents(86400), key=lambda r: r.get("ended", ""),
                             reverse=True)[:20]}


# ── on-demand actions (background jobs triggered from the console) ─────────────
MAX_WEB_SCAN_HOSTS = 8192        # refuse browser-triggered scans larger than this


def _known_operator(op_id: str) -> bool:
    return _valid_session(op_id) is not None


def _job_running(kind: str) -> bool:
    return STORE.job_running(kind)


def _jobs_snapshot(limit: int = 12) -> list:
    return STORE.jobs_snapshot(limit)


def _hms() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def _start_job(kind: str, fn) -> str:
    job_id = uuid.uuid4().hex[:12]
    STORE.job_add({"id": job_id, "kind": kind, "status": "running",
                   "started": _hms(), "finished": None, "summary": None, "error": None})

    def run():
        try:
            STORE.job_update(job_id, status="done", summary=fn(), finished=_hms())
        except Exception as e:
            STORE.job_update(job_id, status="failed", error=str(e)[:300], finished=_hms())

    threading.Thread(target=run, daemon=True).start()
    return job_id


def _trigger_discover(op_id: str, raw_cidrs: str):
    """Validate + launch an on-demand discovery; returns (code, body).

    With Icinga configured, this refreshes the inventory from the Icinga API (no
    CIDRs needed); otherwise it falls back to a scoped network scan of `raw_cidrs`."""
    if not _known_operator(op_id):
        return 401, {"error": "unknown operator — log in first"}
    if _icinga_configured():
        if _job_running("discover"):
            return 409, {"error": "a refresh is already running"}

        def ijob():
            inv = discover_icinga()
            n = sum(len(v) for v in inv.values())
            return f"{len(inv)} host(s), {n} check(s) refreshed from Icinga"

        return 200, {"job_id": _start_job("discover", ijob)}
    cidrs = [c for c in re.split(r"[,\s]+", raw_cidrs or "") if c]
    if not cidrs:
        return 400, {"error": "no CIDRs given"}
    try:
        total = sum(ipaddress.ip_network(c, strict=False).num_addresses for c in cidrs)
    except ValueError as e:
        return 400, {"error": f"bad CIDR: {e}"}
    if total > MAX_WEB_SCAN_HOSTS:
        return 400, {"error": f"range too large ({total} addresses); run scans over "
                              f"{MAX_WEB_SCAN_HOSTS} from the CLI"}
    if _job_running("discover"):
        return 409, {"error": "a discovery is already running"}

    def job():
        inv = discover(cidrs, DEFAULT_SCAN_PORTS)
        return f"{len(inv)} host(s) across {', '.join(cidrs)} — targets updated"

    return 200, {"job_id": _start_job("discover", job)}


def _claim(inc_id: str, op_id: str):
    """Single-owner claim via compare-and-set in the store. Re-claiming by the same
    owner is a no-op success; a different owner gets a conflict with the current owner.
    Claiming before page_at is what suppresses the auto-page — the reconciler only
    pages un-owned, un-acked incidents."""
    name = _op_name(op_id)
    code, res = STORE.incident_claim(inc_id, op_id, name)
    if code == 200:
        cur = STORE.incident_get(inc_id)              # take it out of the hold phase
        if cur and cur.get("phase") in ("triaging", "holding"):
            STORE.incident_update(inc_id, phase="claimed")
        _push_incident_card(inc_id)                   # reflect the claim on the Slack card
    return code, res


def _ack(inc_id: str, op_id: str, idem: str):
    """Idempotent acknowledge. The `idem` key replays a cached result; the actual
    PagerDuty ack side-effect runs exactly once even under concurrent operators —
    the store flags first=True only on the genuine first ack, never on a replay."""
    name = _op_name(op_id)
    code, res = STORE.incident_ack(inc_id, op_id, name, idem)
    if code != 200:
        return code, res
    if res.get("first"):                              # genuine first ack — side-effect once
        inc = STORE.incident_get(inc_id) or {}
        if inc.get("phase") in ("triaging", "holding"):
            STORE.incident_update(inc_id, phase="claimed")
        owner = inc.get("owner_name") or name
        summary = inc.get("summary", "")
        pd_event("acknowledge", inc_id, f"{summary} acknowledged by {owner}", inc.get("severity"))
        if inc.get("ticket"):
            try:
                add_jira_comment(inc["ticket"], _adf_text(f"Acknowledged by {owner}."))
            except Exception:
                pass
        notify("ACK", f"{summary} acked by {owner}")
    _push_incident_card(inc_id)                       # reflect the ack on the Slack card
    return 200, res


def _release(inc_id: str, op_id: str):
    code, res = STORE.incident_release(inc_id, op_id)
    if code == 200:
        _push_incident_card(inc_id)                   # reflect the release on the Slack card
    return code, res


# ── Slack interactive bridge ──────────────────────────────────────────────────
# A Slack button click maps to the same _claim/_ack/_release primitives the web
# console uses. We register the Slack user as an ephemeral operator (keyed by their
# Slack id) so claims/acks attribute to a real name, then push the card update.
def _slack_operator(user_id: str, name: str) -> str:
    op_id = f"slack:{user_id}" if user_id else "slack:unknown"
    STORE.session_create(op_id, name or op_id)        # register (no-op if already present)…
    STORE.session_touch(op_id, SESSION_IDLE_TTL, SESSION_ABS_TTL)  # …and keep it alive
    return op_id


def _incident_card_fields(fp: str):
    """A cheap snapshot of the fields incident_blocks() needs — no Icinga round-trip
    (the interactive path must answer Slack in well under 3s)."""
    i = STORE.incident_get(fp)
    if not i:
        return None
    return {"id": fp, "summary": i.get("summary"), "sig": i.get("sig"),
            "severity": i.get("severity"), "instance": i.get("instance"),
            "service": i.get("service"), "key": i.get("ticket"),
            "owner_name": i.get("owner_name"), "acked": i.get("acked"),
            "paged": i.get("paged"), "phase": i.get("phase", "firing"),
            "hold": i.get("hold_secs", 0), "runbook": i.get("runbook", ""),
            "slack_ts": i.get("slack_ts")}


def _push_incident_card(fp: str):
    """Upsert the incident's Slack card to reflect current state. Best-effort: never
    raises into the caller. Persists the first message ts so any replica updates that
    same message in place."""
    if not cards_enabled():
        return
    fields = _incident_card_fields(fp)
    if not fields:
        return
    try:
        ts = post_incident(fields)
        if ts and not fields.get("slack_ts"):
            STORE.incident_update(fp, slack_ts=ts)
    except Exception as e:
        log_event("SLACK", f"card update failed for {fp}: {e}")


from nanny.web_assets import WEB_CONSOLE_HTML  # peeled to nanny/web_assets.py


class _ServerHandler(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html):
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _raw_body(self):
        # Reject oversized bodies before reading them into memory (DoS guard, F9).
        n = int(self.headers.get("Content-Length", 0) or 0)
        if n > MAX_BODY_BYTES:
            return None
        return self.rfile.read(n) if n else b""

    def _body(self):
        raw = self._raw_body()
        if raw is None:
            return None
        try:
            return json.loads(raw) if raw else {}
        except ValueError:
            return {}

    def _session_token(self) -> str:
        """The bearer session token, taken ONLY from the Authorization header so it
        never lands in URLs/access logs (F2)."""
        h = self.headers.get("Authorization", "")
        return h[7:].strip() if h.startswith("Bearer ") else ""

    def _gate(self):
        """Require a valid session for everything past the public routes. Returns the
        token on success, or sends 401 and returns None."""
        tok = self._session_token()
        if not _valid_session(tok):
            self._send(401, {"error": "authenticate"})
            return None
        _op_touch(tok)
        return tok

    def _slack_action(self, intr):
        """Drive claim/ack/release from a verified Slack button click, then refresh the
        card. We ACK Slack immediately (must reply <3s) and update the message after."""
        verb = SLACK_ACTIONS.get(intr["action"])
        fp = unquote(intr["value"])                   # fingerprints carry '/', %2F on the wire
        if not verb:
            return self._send(200, {"text": f"nanny: unknown action {intr['action']}"})
        op = _slack_operator(intr["user_id"], intr["user_name"])
        if verb == "claim":
            code, _res = _claim(fp, op)
        elif verb == "ack":
            code, _res = _ack(fp, op, f"slack:{intr['user_id']}:{fp}")
        else:
            code, _res = _release(fp, op)
        log_event("SLACK", f"{intr['user_name']} {verb} {fp} via Slack ({code})")
        # _claim/_ack/_release already pushed the card update; just ACK the interaction.
        return self._send(200, {"ok": code == 200})

    def do_POST(self):
        path = self.path.split("?")[0]
        # ── public machine-to-machine webhooks (authenticated by NANNY_WEBHOOK_TOKEN) ──
        if path in ("/", "/alert", "/icinga"):
            raw = self._raw_body()
            if raw is None:
                return self._send(413, {"error": "request body too large"})
            if not _webhook_authorized(self.headers, raw):
                log_event("AUTH", f"webhook rejected ({path}) from {self.client_address[0]} "
                          "— bad/missing token")
                return self._send(401, {"error": "unauthorized"})
            try:
                payload = json.loads(raw) if raw else {}
            except ValueError:
                payload = {}
            self._send(200, {"ok": True})
            if path == "/icinga":                     # Icinga notification webhook
                alert = _alert_from_icinga(payload)
                log_event("ICINGA", f"notification {str(payload.get('type', '?')).upper()} · "
                          f"{alert['labels']['instance']}/{alert['labels']['service']} "
                          f"({alert['status']}, {alert['labels']['severity']})")
                threading.Thread(target=_dispatch_alert, args=(alert,), daemon=True).start()
            else:                                     # Alertmanager webhook
                for alert in payload.get("alerts", []):
                    threading.Thread(target=_dispatch_alert, args=(alert,), daemon=True).start()
            return
        # ── Slack interactivity (button clicks), authenticated by request signature ──
        if path == "/slack/interactive":
            raw = self._raw_body()
            if raw is None:
                return self._send(413, {"error": "request body too large"})
            if not verify_slack_signature(self.headers, raw):
                log_event("AUTH", f"slack interaction rejected from {self.client_address[0]} "
                          "— bad/missing signature")
                return self._send(401, {"error": "unauthorized"})
            intr = parse_interaction(raw)
            if not intr:
                return self._send(400, {"error": "unparseable interaction payload"})
            return self._slack_action(intr)
        b = self._body()
        if b is None:
            return self._send(413, {"error": "request body too large"})
        # ── login / logout (public) ──
        if path == "/api/login":
            ip = self.client_address[0]
            if _login_blocked(ip):
                log_event("AUTH", f"login rate-limited: {ip}")
                return self._send(429, {"error": "too many login attempts — wait a few minutes"})
            name = (b.get("name", "") or "").strip()
            mode = _auth_mode()
            ok, disp, err = _login_check(mode, name, b.get("password", ""))
            if not ok:
                if err == "not-configured":           # config issue, not a brute-force attempt
                    return self._send(403, {"error": "authentication not configured — set "
                                            "NANNY_LDAP_URL or NANNY_AUTH_PASSWORD (or "
                                            "NANNY_AUTH_INSECURE=true to allow name-only access)"})
                _login_record_fail(ip)
                log_event("AUTH", f"login denied for {name or '?'} from {ip}: {err}")
                return self._send(401, {"error": err or "authentication failed"})
            _login_clear(ip)
            log_event("AUTH", f"login ok ({mode}): {disp} from {ip}")
            return self._send(200, {"token": _op_login(disp), "name": disp})
        if path == "/api/logout":
            _op_logout(self._session_token())
            return self._send(200, {"ok": True})
        # ── everything below requires a valid session ──
        tok = self._gate()
        if tok is None:
            return
        if path in ("/api/test/page", "/api/test/resolve"):
            if not _dry():
                return self._send(400, {"error": "set NANNY_DRY_RUN=true to use test mode"})
            resolve = path.endswith("resolve")
            alert = _alert_from_icinga({
                "type": "RECOVERY" if resolve else "PROBLEM",
                "host": b.get("host", "test-host-01"),
                "service": b.get("service", "synthetic-check"),
                "state": (b.get("state") or ("OK" if resolve else "CRITICAL")).upper(),
                "output": b.get("output", "SIMULATED recovery" if resolve
                                else "SIMULATED - injected by nanny test mode"),
                "address": b.get("host", "test-host-01"),
                "hold": 0 if resolve else b.get("hold", 8)})
            log_event("TEST", ("injected recovery: " + alert["fingerprint"]) if resolve
                      else (f"injected synthetic page: {alert['labels']['instance']}/"
                            f"{alert['labels']['service']} {alert['labels']['severity']} "
                            f"hold={alert['_hold']}s"))
            threading.Thread(target=_dispatch_alert, args=(alert,), daemon=True).start()
            return self._send(200, {"ok": True, "fingerprint": alert["fingerprint"]})
        if path == "/api/test/integration":
            name = (b.get("name", "") or "").strip().lower()
            res = run_integration_check(name)
            if res is None:
                return self._send(404, {"error": f"unknown integration: {name or '(blank)'}"})
            log_event("TEST", f"integration {res['label']}: {res['status']} — {res['detail']}")
            return self._send(200, res)
        if path == "/api/heartbeat":
            return self._send(200, {"ok": True})
        m = re.fullmatch(r"/api/incidents/([^/]+)/(claim|ack|release)", path)
        if m:
            inc_id, action = unquote(m.group(1)), m.group(2)   # '/'-bearing fingerprints arrive %2F-encoded
            if action == "claim":
                return self._send(*_claim(inc_id, tok))
            if action == "ack":
                return self._send(*_ack(inc_id, tok, b.get("idem", "")))
            return self._send(*_release(inc_id, tok))
        if path == "/api/actions/discover":
            return self._send(*_trigger_discover(tok, b.get("cidrs", "")))
        self._send(404, {"error": "not found"})

    def do_GET(self):
        path = self.path.split("?")[0]
        qs = dict(p.split("=", 1) for p in self.path.partition("?")[2].split("&") if "=" in p)
        # ── public routes (no session needed: console shell + what the gate needs) ──
        if path in ("/", "/console"):
            return self._send_html(WEB_CONSOLE_HTML)
        if path == "/healthz":                        # liveness: process is up
            return self._send(200, {"ok": True})
        if path == "/readyz":                         # readiness: backing store reachable
            ok = STORE.healthy()
            return self._send(200 if ok else 503, {"ok": ok, "store": ok})
        if path == "/api/config":
            return self._send(200, {"fleet_source": "icinga" if _icinga_configured() else "cidr",
                                    "dry_run": _dry(), "auth_required": _auth_enabled(),
                                    "auth_mode": _auth_mode(),
                                    "llm_enabled": _llm_enabled(),
                                    "triage": _llm_backend() if _llm_enabled() else "deterministic (no-LLM)",
                                    "jira_base": _env_dir("JIRA_BASE_URL", "").rstrip("/")})
        # ── everything below requires a valid session (F3: reads gated too) ──
        if self._gate() is None:
            return
        if path == "/api/state":
            return self._send(200, {"incidents": _incidents_snapshot(),
                                    "operators": _online_operators(),
                                    "busy": _busy_count()})
        m = re.fullmatch(r"/api/incidents/([^/]+)", path)
        if m:
            d = _incident_detail(unquote(m.group(1)))   # fingerprints contain '/', sent %2F-encoded
            return self._send(200 if d else 404, d or {"error": "incident not found (resolved?)"})
        if path == "/api/integrations":
            return self._send(200, {"integrations": _integrations_status()})
        if path == "/api/fleet":
            return self._send(200, {"fleet": _fleet_status()})
        if path == "/api/reports":
            return self._send(200, _reports_payload())
        if path == "/api/series":
            try:
                win, bk = int(qs.get("window", "86400")), int(qs.get("bucket", "3600"))
            except ValueError:
                win, bk = 86400, 3600
            bk = max(300, bk)
            win = max(bk, min(win, 30 * 86400))
            return self._send(200, {"series": _series_payload(win, bk)})
        if path == "/api/jobs":
            return self._send(200, {"jobs": _jobs_snapshot()})
        if path == "/api/log":
            try:
                since = int(qs.get("since", "0"))
            except ValueError:
                since = 0
            return self._send(200, {"log": _log_snapshot(since)})
        self._send(404, {"error": "not found"})

    def log_message(self, *_):                        # quiet the default access log
        pass


def server():
    """Always-on backend: Alertmanager webhook + operator API + shared incident state."""
    global anthropic_client
    if _llm_enabled() and _llm_backend() == "anthropic":
        anthropic_client = anthropic.Anthropic()
    STORE.init_schema()                               # idempotent; no-op for MemoryStore
    # Every replica runs the paging reconciler; the store's atomic claim makes the
    # delayed page exactly-once with no leader election (see _reconciler_loop).
    threading.Thread(target=_reconciler_loop, daemon=True).start()
    if _demo():
        _demo_seed_session()                          # ntfy action-button auth
        threading.Thread(target=_demo_keepalive_loop, daemon=True).start()  # keep it alive
        if _env_flag("NANNY_DEMO_SCENARIO"):
            threading.Thread(target=_demo_scenario_loop, daemon=True).start()
    port = int(os.environ.get("NANNY_SERVER_PORT", os.environ.get("NANNY_WEBHOOK_PORT", "9099")))
    src = "Icinga" if _icinga_configured() else "Prometheus"
    triage = _llm_backend() if _llm_enabled() else "deterministic (no-LLM)"
    mode = _auth_mode()
    auth_desc = {"ldap": "LDAP bind", "password": "shared console password",
                 "open": "NAME-ONLY (insecure)"}[mode]
    log_event("INFO", f"nanny server started on :{port} — fleet source: {src} — "
              f"triage: {triage} — auth: {auth_desc}")
    if mode == "open":
        if _auth_open_allowed():
            print(_c("server: WARNING — console auth is name-only (NANNY_AUTH_INSECURE=true). "
                     "Anyone who can reach this port can mint a session. Set NANNY_LDAP_URL or "
                     "NANNY_AUTH_PASSWORD for real auth, and keep the port off untrusted networks.\n",
                     "1;31"))
        else:
            print(_c("server: no auth configured — logins are REFUSED. Set NANNY_LDAP_URL or "
                     "NANNY_AUTH_PASSWORD, or NANNY_AUTH_INSECURE=true for name-only access.\n", "1;31"))
    if not _webhook_secret():
        print(_c("server: WARNING — webhooks (/alert, /icinga) are UNAUTHENTICATED. Set "
                 "NANNY_WEBHOOK_TOKEN and have Alertmanager/Icinga send it, or any network peer "
                 "can forge pages and open tickets.\n", "1;31"))
    print(_c(f"server: listening on :{port}  (web console at /, POST /alert, /api/*) — auth: {auth_desc}\n", "1"))
    ThreadingHTTPServer(("", port), _ServerHandler).serve_forever()


# ── demo mode ─────────────────────────────────────────────────────────────────
# A self-contained showcase: synthetic alerts drive the full discover→triage→hold→
# page→resolve lifecycle, ntfy stands in for Slack/PagerDuty, the fleet is fabricated,
# and side effects run dry (fake DRY-xxxx tickets) — so the whole thing runs end-to-end
# with no real integrations or secrets. See deploy/demo/ for the container + wiring.

# (instance, [(service, state, output)])  state: 0 OK · 1 WARN · 2 CRIT · 3 UNKNOWN
_DEMO_HOSTS = [
    ("web-01.demo", [("ping", 0, "PING OK - 0.42ms"),
                     ("api-5xx", 0, "HTTP OK - 0.2% 5xx over 5m"),
                     ("disk-root", 0, "DISK OK - 41% used")]),
    ("web-02.demo", [("ping", 0, "PING OK - 0.39ms"),
                     ("api-5xx", 0, "HTTP OK - 0.1% 5xx over 5m")]),
    ("db-01.demo", [("ping", 0, "PING OK - 0.51ms"),
                    ("replication-lag", 0, "REPL OK - lag 0.3s"),
                    ("connections", 1, "WARNING - 182/200 connections")]),
    ("db-02.demo", [("ping", 0, "PING OK - 0.55ms"),
                    ("replication-lag", 0, "REPL OK - lag 0.4s")]),
    ("cache-01.demo", [("ping", 0, "PING OK - 0.33ms"),
                       ("memory", 0, "MEM OK - 62% used")]),
]


def _demo_entry(service: str, state: int, output: str) -> dict:
    """One fabricated fleet row in the same shape Icinga's _icinga_entry produces."""
    return {"service": service, "state": state, "up": state == 0, "state_type": "hard",
            "output": output, "perf": "", "age": "—", "last_check": "just now",
            "attempt": "1/1", "command": "demo", "acked": False, "downtime": False,
            "notes": "", "notes_url": ""}


def _demo_fleet() -> list:
    """Fabricated fleet so the console's Fleet tab is populated without Icinga. Active
    demo incidents are overlaid as CRIT so the firing host visibly turns red."""
    firing = {}
    for inc in _incidents_snapshot():
        if inc.get("phase") != "resolved":
            firing[(inc.get("instance"), inc.get("service"))] = inc.get("summary", "")
    rows = []
    for inst, svcs in _DEMO_HOSTS:
        host = _demo_entry("host", 0, "Host alive")
        host.update({"instance": inst, "host_display": inst})
        rows.append(host)
        for svc, st, out in svcs:
            hit = firing.get((inst, svc))
            if hit:
                st, out = 2, hit
            row = _demo_entry(svc, st, out)
            row["instance"] = inst
            rows.append(row)
    return rows


def _demo_fire(host: str, service: str, output: str, hold: int) -> dict:
    return _alert_from_icinga({"type": "PROBLEM", "host": host, "service": service,
                               "state": "CRITICAL", "output": output,
                               "address": host, "hold": hold})


def _demo_resolve(host: str, service: str, output: str) -> dict:
    return _alert_from_icinga({"type": "RECOVERY", "host": host, "service": service,
                               "state": "OK", "output": output, "address": host})


def _demo_speed() -> float:
    """Wall-clock multiplier for the demo timeline (NANNY_DEMO_SPEED). >1 stretches the
    fire→hold→page→resolve arc for a more narratable pace; <1 speeds it up. The whole
    timeline (sleeps AND adaptive holds) scales together so the sequence stays valid.
    Clamped to a sane range; the reconciler's 5s tick sets the practical floor."""
    try:
        s = float(_env_dir("NANNY_DEMO_SPEED", "1"))
    except ValueError:
        s = 1.0
    return min(12.0, max(0.25, s))


def _demo_run_once():
    """One pass of the scripted timeline: one incident self-resolves before paging,
    one holds then pages (to ntfy) and recovers after. Scales with NANNY_DEMO_SPEED
    (1× ≈ 33s active; e.g. 3× ≈ 100s for a relaxed, walk-through-able demo)."""
    _demo_seed_session()                              # keep the ntfy-button token alive
    k = _demo_speed()
    def nap(sec):  time.sleep(sec * k)                # scaled pause
    def hold(sec): return int(sec * k)               # scaled adaptive-hold window
    # A — fires, then self-resolves before its (long) hold elapses → never pages.
    _dispatch_alert(_demo_fire("web-01.demo", "api-5xx",
                    "HTTP CRITICAL - 5xx rate 38% over 2m (upstream timeouts)", hold=hold(60)))
    nap(5)
    # B — fires with a shorter hold → the reconciler pages on-call (ntfy), then recovers.
    _dispatch_alert(_demo_fire("db-02.demo", "replication-lag",
                    "REPL CRITICAL - replica lag 240s and climbing", hold=hold(12)))
    nap(10)
    _dispatch_alert(_demo_resolve("web-01.demo", "api-5xx",
                    "HTTP OK - 5xx rate back to 0.2%"))        # self-resolved (no page)
    nap(18)                                                    # let B's hold page + sit
    _dispatch_alert(_demo_resolve("db-02.demo", "replication-lag",
                    "REPL OK - lag 0.4s"))                    # recovered after paging


def _demo_scenario_loop():
    log_event("DEMO", "scenario driver started — injecting synthetic incidents on a loop")
    try:
        time.sleep(float(_env_dir("NANNY_DEMO_START_DELAY", "8")))   # let subscribers connect
    except ValueError:
        time.sleep(8)
    while True:
        try:
            _demo_run_once()
        except Exception as e:
            log_event("DEMO", f"scenario error: {type(e).__name__}: {e}")
        try:
            time.sleep(int(_env_dir("NANNY_DEMO_LOOP_PAUSE", "30")))
        except ValueError:
            time.sleep(30)


def _demo_seed_session():
    """Pre-seed a session for the ntfy action-button token so a phone tap on Ack/Claim
    authenticates against the normal operator endpoints (no separate auth path)."""
    token = os.environ.get("NANNY_DEMO_ACTION_TOKEN")
    if token:
        STORE.session_create(token, "ntfy-phone")


def _demo_keepalive_loop():
    """Re-seed the action-token session on a timer so it never idle-expires. Without
    this, in on-demand mode (scenario off) nothing refreshes the session and after the
    30-min idle TTL the ntfy Ack/Claim buttons AND the interactive deck start 401-ing.
    Re-seeding resets created+last_seen, so the demo session stays valid indefinitely."""
    while True:
        try:
            time.sleep(600)
            _demo_seed_session()
        except Exception as e:
            log_event("DEMO", f"keepalive error: {e}")


def _demo_persist(name: str, gen):
    """Read a stable demo value from the data dir, generating + persisting it once so
    the ntfy topic / action token survive restarts (subscribers stay subscribed)."""
    p = _data_path(name)
    try:
        v = open(p).read().strip()
        if v:
            return v
    except OSError:
        pass
    v = gen()
    try:
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        with open(p, "w") as fh:
            fh.write(v)
    except OSError:
        pass
    return v


def _demo_banner():
    topic, base = ntfy_topic(), ntfy_base()
    pub = _env_dir("NANNY_PUBLIC_URL", "")
    port = os.environ.get("NANNY_SERVER_PORT", os.environ.get("NANNY_WEBHOOK_PORT", "9099"))
    print(_c("\n  nanny — DEMO MODE\n", "1;36"))
    print(f"  console     : http://localhost:{port}/   (login: any name · password 'demo')")
    print(f"  ntfy topic  : {base}/{topic}")
    print(f"  subscribe   : open the ntfy app (or {base}/{topic} in a browser) and add "
          f"topic '{topic}'")
    if ntfy_interactive():
        print(f"  buttons     : ON — Ack/Claim call back to {pub} (must be reachable from your phone)")
    else:
        print(_c("  buttons     : OFF — set NANNY_PUBLIC_URL to your LAN/tunnel URL to enable "
                 "tap-to-Ack/Claim", "2"))
    print(_c("  side effects: dry-run (no real Jira/PagerDuty/Slack) · triage: "
             f"{'Claude' if _llm_enabled() else 'deterministic (no-LLM)'}\n", "2"))


def demo():
    """Self-contained demo: synthetic incidents + ntfy, no real integrations needed.
    Sets safe defaults (override any via env), then runs the normal server with the
    scenario driver enabled."""
    os.environ["NANNY_DEMO"] = "true"
    os.environ.setdefault("NANNY_DRY_RUN", "true")        # stub Jira/PD/Slack → DRY tickets
    os.environ.setdefault("NANNY_LLM_BACKEND", "none")    # offline triage, no API key/cost
    os.environ.setdefault("NANNY_AUTH_PASSWORD", "demo")  # console: any name + 'demo'
    os.environ.setdefault("NANNY_DEMO_SCENARIO", "true")  # self-running timeline
    if not ntfy_topic():
        os.environ["NANNY_NTFY_TOPIC"] = _demo_persist(
            "demo_topic.txt", lambda: "nanny-demo-" + secrets.token_hex(4))
    if not os.environ.get("NANNY_DEMO_ACTION_TOKEN"):
        os.environ["NANNY_DEMO_ACTION_TOKEN"] = _demo_persist(
            "demo_token.txt", lambda: secrets.token_urlsafe(32))
    _demo_banner()
    server()


# ── shift reports ─────────────────────────────────────────────────────────────


def report(since: str):
    """Summarize incidents from nanny's log over the window into a shift report."""
    incidents = _read_incidents(_parse_window(since))
    s = _summarize(incidents)
    out = [f"Shift report — last {since}",
           f"incidents: {s['total']}   paged: {s['paged']}   "
           f"self-resolved: {s['self_resolved']}   MTTR: {s['mttr']}s"]
    if s["by_service"]:
        out.append("by service: " + ", ".join(
            f"{k}={v}" for k, v in sorted(s["by_service"].items(), key=lambda kv: -kv[1])))
    out.append("")
    for r in sorted(incidents, key=lambda r: r.get("ended", "")):
        flag = "PAGED" if r["paged"] else "self-resolved"
        out.append(f"  {r.get('ended', '')[:19]}  {r.get('sig')}  {r.get('instance') or ''}  "
                   f"{r['duration']}s  {flag}  {r.get('ticket') or ''}")
    text = "\n".join(out)
    print(text)

    channel, token = os.environ.get("SLACK_ALERT_CHANNEL"), os.environ.get("SLACK_BOT_TOKEN")
    if channel and token:
        try:
            WebClient(token=token).chat_postMessage(channel=channel, text=f"```\n{text}\n```")
        except Exception as e:
            print(_c(f"slack: report post failed: {e}", "2"))


# ── terminal UI (live multi-view console) ────────────────────────────────────
# Pulls live state from the bundle's Prometheus (:9090) and Alertmanager (:9093)
# plus nanny's own incident log. Read-only — it's a window, not a control panel.

# (group, [(env_var, is_secret)]) — drives the settings view. Secrets are shown
# only as set/not-set; non-secret config shows its value.
SETTINGS_SPEC = [
    ("Anthropic", [("ANTHROPIC_API_KEY", True)]),
    ("Icinga", [("ICINGA_API_URL", False), ("ICINGA_API_USER", False),
                ("ICINGA_API_PASSWORD", True), ("ICINGA_API_CA", False),
                ("ICINGA_INSECURE", False), ("ICINGA_LEGACY_TLS", False),
                ("ICINGA_RETRIES", False)]),
    ("Slack", [("SLACK_BOT_TOKEN", True), ("SLACK_ALERT_CHANNEL", False),
               ("SLACK_APP_TOKEN", True), ("PAGERDUTY_SLACK_BOT_ID", False)]),
    ("Atlassian", [("ATLASSIAN_EMAIL", False), ("ATLASSIAN_API_TOKEN", True),
                   ("JIRA_BASE_URL", False), ("CONFLUENCE_BASE_URL", False)]),
    ("Jira tickets", [("JIRA_PROJECT_KEY", False), ("JIRA_ISSUE_TYPE", False),
                      ("JIRA_DONE_STATUS", False), ("JIRA_DONE_TRANSITION_ID", False),
                      ("NANNY_AUTOCLOSE", False)]),
    ("PagerDuty", [("PAGERDUTY_API_TOKEN", True), ("PAGERDUTY_FROM_EMAIL", False),
                   ("PAGERDUTY_ROUTING_KEY", True), ("PAGERDUTY_API_BASE", False)]),
    ("Runtime", [("NANNY_DATA_DIR", False), ("NANNY_WEBHOOK_PORT", False),
                 ("PROMETHEUS_URL", False), ("ALERTMANAGER_URL", False)]),
]


def _read_incidents(since_secs: int) -> list:
    return STORE.read_incidents(since_secs)


def _summarize(incidents: list) -> dict:
    durs = [r["duration"] for r in incidents]
    by_service = {}
    for r in incidents:
        k = r.get("service") or "?"
        by_service[k] = by_service.get(k, 0) + 1
    return {"total": len(incidents),
            "paged": sum(r["paged"] for r in incidents),
            "self_resolved": sum(r["self_resolved"] for r in incidents),
            "mttr": round(sum(durs) / len(durs)) if durs else 0,
            "by_service": by_service}


def _series_payload(window_secs: int, bucket_secs: int) -> list:
    """Bucket resolved incidents into time bins for the trend charts: per bucket,
    counts of paged vs self-resolved and the mean time-to-resolution."""
    incs = _read_incidents(window_secs)
    nb = max(1, window_secs // bucket_secs)
    start = time.time() - nb * bucket_secs
    bins = [{"paged": 0, "self_resolved": 0, "durs": []} for _ in range(nb)]
    for r in incs:
        try:
            ended = datetime.fromisoformat(r["ended"].replace("Z", "+00:00")).timestamp()
        except (ValueError, KeyError, TypeError):
            continue
        idx = int((ended - start) // bucket_secs)
        if 0 <= idx < nb:
            b = bins[idx]
            b["paged"] += 1 if r.get("paged") else 0
            b["self_resolved"] += 1 if r.get("self_resolved") else 0
            b["durs"].append(r.get("duration", 0))
    fmt = "%H:%M" if bucket_secs < 86400 else "%m/%d"
    out = []
    for i, b in enumerate(bins):
        t = datetime.fromtimestamp(start + i * bucket_secs, timezone.utc)
        out.append({"short": t.strftime(fmt), "label": t.isoformat(timespec="minutes"),
                    "paged": b["paged"], "self_resolved": b["self_resolved"],
                    "mttr": round(sum(b["durs"]) / len(b["durs"])) if b["durs"] else 0})
    return out


def _prom_query(expr: str) -> list:
    url = os.environ.get("PROMETHEUS_URL", "http://localhost:9090")
    try:
        r = requests.get(f"{url}/api/v1/query", params={"query": expr}, timeout=4)
        return r.json().get("data", {}).get("result", []) if r.ok else []
    except requests.RequestException:
        return []


_FLEET_CACHE = {"t": 0.0, "rows": []}
_fleet_lock = threading.Lock()


def _fleet_status() -> list:
    # Demo mode with no Icinga: serve a fabricated fleet so the view isn't empty.
    if _demo() and not _icinga_configured():
        return _demo_fleet()
    # Icinga is the source of truth when configured: read live host/service state
    # straight from its API. Falls back to the Prometheus `probe_success` metric for
    # legacy network-scan + blackbox deployments.
    if _icinga_configured():
        # The console polls this every few seconds; a full hosts+services pull from
        # Icinga is expensive, so cache for NANNY_FLEET_TTL (default 15s). On a slow
        # refresh another caller serves the cached rows; on error we serve stale.
        try:
            ttl = max(0, int(_env_dir("NANNY_FLEET_TTL", "15")))
        except ValueError:
            ttl = 15
        now = time.time()
        c = _FLEET_CACHE
        if c["rows"] and now - c["t"] < ttl:
            return c["rows"]
        if not _fleet_lock.acquire(blocking=False):
            return c["rows"]                 # a refresh is already in flight
        try:
            inv = icinga_inventory()
            c["rows"] = [{**e, "instance": addr}
                         for addr, svcs in inv.items() for e in svcs]
            c["t"] = time.time()
            return c["rows"]
        except Exception:
            return c["rows"]                 # serve stale rather than blank the view
        finally:
            _fleet_lock.release()
    rows = []
    for s in _prom_query("probe_success"):
        m = s.get("metric", {})
        rows.append({"instance": m.get("instance", "?"), "service": m.get("service", ""),
                     "up": s.get("value", [None, "0"])[1] == "1"})
    return rows


def _am_active() -> list:
    url = os.environ.get("ALERTMANAGER_URL", "http://localhost:9093")
    try:
        r = requests.get(f"{url}/api/v2/alerts",
                         params={"active": "true", "silenced": "false", "inhibited": "false"},
                         timeout=4)
        return r.json() if r.ok else []
    except (requests.RequestException, ValueError):
        return []


def _age(iso: str) -> str:
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        s = int((datetime.now(timezone.utc) - t).total_seconds())
    except (ValueError, TypeError):
        return "?"
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    return f"{s // 3600}h{(s % 3600) // 60}m"


def _read_keys(state: dict):
    """Background single-key reader to switch views without blocking refresh."""
    import termios
    import tty
    import select
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    keymap = {"d": "dashboard", "f": "fleet", "i": "incidents", "r": "reports",
              "s": "settings", "a": "about"}
    try:
        tty.setcbreak(fd)
        while not state["quit"]:
            if select.select([sys.stdin], [], [], 0.25)[0]:
                ch = sys.stdin.read(1).lower()
                if ch == "q":
                    state["quit"] = True
                elif ch in keymap:
                    state["view"] = keymap[ch]
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def tui():
    """Live, multi-view monitoring console: dashboard / fleet / incidents / reports."""
    if not sys.stdin.isatty():
        sys.exit("tui: needs an interactive terminal (don't pipe it).")
    from rich.live import Live
    from rich.console import Group
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    def status_cell(up):
        return Text("● UP", style="green") if up else Text("● DOWN", style="bold red")

    def outcome_cell(rec):
        return Text("PAGED", style="red") if rec["paged"] else Text("self-resolved", style="green")

    def ticket_cell(rec):
        return (rec.get("ticket") or "") + (" ✓" if rec.get("closed") else "")

    def recent_table(incidents, n):
        t = Table(expand=True)
        for c in ("time", "signature", "instance", "dur", "outcome", "ticket"):
            t.add_column(c)
        for r in sorted(incidents, key=lambda r: r.get("ended", ""), reverse=True)[:n]:
            t.add_row(r.get("ended", "")[11:19], r.get("sig", ""), r.get("instance") or "",
                      f"{r['duration']}s", outcome_cell(r), ticket_cell(r))
        return t

    def body_dashboard():
        fleet = _fleet_status()
        up, tot = sum(f["up"] for f in fleet), len(fleet)
        active = _am_active()
        day = _summarize(_read_incidents(86400))
        g = Table.grid(padding=(0, 4))
        g.add_column(style="bold")
        g.add_column()
        g.add_row("fleet", Text(f"{up}/{tot} up", style="green" if tot and up == tot else "yellow")
                  if tot else Text("n/a — Prometheus unreachable", style="dim"))
        g.add_row("firing now", Text(str(len(active)), style="bold red" if active else "green"))
        g.add_row("last 24h", Text(f"{day['total']} handled · {day['paged']} paged · "
                                   f"{day['self_resolved']} self-resolved · MTTR {day['mttr']}s"))
        return Group(Panel(g, title="overview", border_style="cyan"),
                     Panel(recent_table(_read_incidents(86400), 6),
                           title="recent incidents (24h)", border_style="cyan"))

    def body_fleet():
        fleet = sorted(_fleet_status(), key=lambda f: (f["up"], f["instance"]))
        if not fleet:
            url = os.environ.get("PROMETHEUS_URL", "http://localhost:9090")
            return Panel(Text(f"No probe data — is Prometheus reachable at {url}?", style="dim"),
                         title="fleet")
        t = Table(expand=True)
        for c in ("status", "instance", "service"):
            t.add_column(c)
        for f in fleet:
            t.add_row(status_cell(f["up"]), f["instance"], f["service"])
        downs = sum(not f["up"] for f in fleet)
        return Panel(t, title=f"fleet — {len(fleet)} probes, {downs} down",
                     border_style="red" if downs else "cyan")

    def body_incidents():
        active = _am_active()
        at = Table(expand=True, title="firing now")
        for c in ("since", "alert", "service", "instance", "severity"):
            at.add_column(c)
        for a in active:
            lbl = a.get("labels", {})
            at.add_row(_age(a.get("startsAt", "")), lbl.get("alertname", ""), lbl.get("service", ""),
                       lbl.get("instance", ""), lbl.get("severity", ""))
        if not active:
            at.add_row("—", "(nothing firing)", "", "", "")
        return Group(Panel(at, border_style="red" if active else "cyan"),
                     Panel(recent_table(_read_incidents(86400), 12),
                           title="resolved (24h)", border_style="cyan"))

    def body_reports():
        windows = [("8h", 8 * 3600), ("24h", 86400), ("7d", 7 * 86400)]
        summ = [(lbl, _summarize(_read_incidents(secs))) for lbl, secs in windows]
        t = Table(expand=True)
        for c in ("window", "incidents", "paged", "self-resolved", "MTTR"):
            t.add_column(c)
        for lbl, s in summ:
            t.add_row(lbl, str(s["total"]), str(s["paged"]), str(s["self_resolved"]), f"{s['mttr']}s")
        s24 = summ[1][1]
        bs = Table(expand=True)
        bs.add_column("service")
        bs.add_column("incidents (24h)")
        for k, v in sorted(s24["by_service"].items(), key=lambda kv: -kv[1]) or [("(none)", 0)]:
            bs.add_row(str(k), str(v))
        return Group(Panel(t, title="shift summary", border_style="cyan"),
                     Panel(bs, title="by service (24h)", border_style="cyan"))

    def body_settings():
        t = Table(expand=True)
        t.add_column("variable", style="bold")
        t.add_column("status / value")
        first = True
        for group, vars_ in SETTINGS_SPEC:
            if not first:
                t.add_section()
            first = False
            t.add_row(Text(group, style="cyan"), "")
            for var, secret in vars_:
                val = os.environ.get(var)
                if not val:
                    cell = Text("— not set", style="dim")
                elif secret:
                    cell = Text("set", style="green")          # never print secrets
                else:
                    cell = Text(val, style="green")
                t.add_row(f"  {var}", cell)
        return Panel(t, title="settings — read from the environment", border_style="cyan",
                     subtitle="edit nanny.env and restart to change · secrets shown only as set/not-set")

    def body_about():
        link = AUTHOR["linkedin"]
        content = Group(
            Text(f"nanny  v{NANNY_VERSION}", style="bold cyan"),
            Text(PROJECT_BLURB),
            Text(""),
            Text("Created by " + AUTHOR["name"], style="bold"),
            Text(AUTHOR["title"], style="dim"),
            Text(link, style=f"link {link} blue underline"),
            Text(""),
            Text("Runs on the Anthropic API · Prometheus · Alertmanager · "
                 "blackbox_exporter (Apache-2.0)", style="dim"),
        )
        return Panel(content, title="about", border_style="cyan", padding=(1, 2))

    bodies = {"dashboard": body_dashboard, "fleet": body_fleet,
              "incidents": body_incidents, "reports": body_reports,
              "settings": body_settings, "about": body_about}

    def render(view):
        now = datetime.now().strftime("%H:%M:%S")
        header = Panel(Text(f"nanny  ·  {view}", style="bold cyan"), subtitle=f"updated {now}")
        footer = Panel(Text("[d]ashboard  [f]leet  [i]ncidents  [r]eports  "
                            "[s]ettings  [a]bout      [q]uit", style="dim"))
        return Group(header, bodies.get(view, body_dashboard)(), footer)

    state = {"view": "dashboard", "quit": False}
    threading.Thread(target=_read_keys, args=(state,), daemon=True).start()
    with Live(render(state["view"]), screen=True, auto_refresh=False) as live:
        while not state["quit"]:
            live.update(render(state["view"]))
            live.refresh()
            time.sleep(1.5)


# ── operator control panel (multi-operator client of the server) ─────────────
def _panel_api(method, server_url, path, payload=None, params=None, token=None):
    url = server_url.rstrip("/") + path
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        if method == "GET":
            r = requests.get(url, params=params, headers=headers, timeout=5)
        else:
            r = requests.post(url, json=payload, headers=headers, timeout=5)
        return r.status_code, (r.json() if r.content else {})
    except requests.RequestException as e:
        return 0, {"error": str(e)}


def _panel_login(server_url, name):
    """Log the CLI client in, prompting for a password when the server requires one.
    Returns the session token, or exits on failure."""
    _, cfg = _panel_api("GET", server_url, "/api/config")
    pw = ""
    if cfg.get("auth_required"):
        import getpass
        pw = os.environ.get("NANNY_AUTH_PASSWORD") or getpass.getpass("password: ")
    code, res = _panel_api("POST", server_url, "/api/login", {"name": name, "password": pw})
    if code != 200:
        sys.exit(f"panel: login to {server_url} failed ({res.get('error', code)})")
    return res["token"]


def panel(server_url: str = None, name: str = None):
    """Operator control panel: shared incidents, online operators, claim/ack."""
    if not sys.stdin.isatty():
        sys.exit("panel: needs an interactive terminal.")
    server_url = server_url or os.environ.get("NANNY_SERVER_URL")
    if not server_url:
        sys.exit("panel: server address required — pass --server http://nanny-host:9099 "
                 "or set NANNY_SERVER_URL.")
    server_url = server_url.rstrip("/")
    name = name or os.environ.get("NANNY_OPERATOR") or input("operator name: ").strip() or "operator"
    op_id = _panel_login(server_url, name)

    from rich.live import Live
    from rich.console import Group
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    state = {"view": "incidents", "quit": False, "sel": 0, "status": "",
             "incidents": [], "operators": []}

    def heartbeat():
        while not state["quit"]:
            _panel_api("POST", server_url, "/api/heartbeat", token=op_id)
            time.sleep(10)

    def poll():
        while not state["quit"]:
            code, res = _panel_api("GET", server_url, "/api/state", token=op_id)
            if code == 200:
                state["incidents"] = res.get("incidents", [])
                state["operators"] = res.get("operators", [])
            time.sleep(2)

    def act(action):
        inc = state["incidents"][state["sel"]] if state["incidents"] else None
        if not inc:
            state["status"] = "no incident selected"
            return
        payload = {}
        if action == "ack":
            payload["idem"] = uuid.uuid4().hex      # idempotency key per click
        code, res = _panel_api("POST", server_url,
                               f"/api/incidents/{quote(inc['id'], safe='')}/{action}",
                               payload, token=op_id)
        if code == 200:
            state["status"] = f"{action}: {inc['summary'][:40]} (owner {res.get('owner') or '—'})"
        elif code == 409:
            state["status"] = f"already claimed by {res.get('owner')}"
        else:
            state["status"] = f"{action} failed ({res.get('error', code)})"

    def keys():
        import termios
        import tty
        import select
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        nav = {"i": "incidents", "o": "operators", "d": "dashboard"}
        try:
            tty.setcbreak(fd)
            while not state["quit"]:
                if not select.select([sys.stdin], [], [], 0.25)[0]:
                    continue
                ch = sys.stdin.read(1).lower()
                if ch == "q":
                    state["quit"] = True
                elif ch in nav:
                    state["view"] = nav[ch]
                elif ch.isdigit() and ch != "0":
                    idx = int(ch) - 1
                    if idx < len(state["incidents"]):
                        state["sel"] = idx
                elif ch == "c":
                    act("claim")
                elif ch == "k":
                    act("ack")
                elif ch == "x":
                    act("release")
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

    def body_incidents():
        t = Table(expand=True)
        for c in ("#", "severity", "summary", "service", "owner", "ack", "age", "ticket"):
            t.add_column(c)
        for n, inc in enumerate(state["incidents"]):
            sev = Text(inc["severity"], style="red" if inc["severity"] == "critical" else "yellow")
            owner = Text(inc["owner"], style="green") if inc["owner"] else Text("—", style="dim")
            ack = Text("✓", style="green") if inc["acked"] else Text("—", style="dim")
            idx = Text(f"{n+1}", style="reverse" if n == state["sel"] else "")
            t.add_row(idx, sev, inc["summary"][:48], inc["service"], owner, ack,
                      f"{inc['age']}s", inc.get("ticket") or "")
        if not state["incidents"]:
            t.add_row("", "", "(nothing firing)", "", "", "", "", "")
        return Panel(t, title=f"incidents — {len(state['incidents'])} firing", border_style="cyan")

    def body_operators():
        t = Table(expand=True)
        t.add_column("operator")
        t.add_column("idle")
        for o in state["operators"]:
            t.add_row(Text("● " + o["name"], style="green"), f"{o['idle']}s")
        if not state["operators"]:
            t.add_row("(none online)", "")
        return Panel(t, title=f"online operators — {len(state['operators'])}", border_style="cyan")

    def body_dashboard():
        inc = state["incidents"]
        unclaimed = sum(1 for i in inc if not i["owner"])
        g = Table.grid(padding=(0, 4))
        g.add_column(style="bold")
        g.add_column()
        g.add_row("firing now", Text(str(len(inc)), style="bold red" if inc else "green"))
        g.add_row("unclaimed", Text(str(unclaimed), style="red" if unclaimed else "green"))
        g.add_row("online operators", str(len(state["operators"])))
        return Group(Panel(g, title="overview", border_style="cyan"), body_incidents())

    bodies = {"incidents": body_incidents, "operators": body_operators, "dashboard": body_dashboard}

    def render():
        online = len(state["operators"])
        header = Panel(Text(f"nanny control panel  ·  {name}  ·  {state['view']}", style="bold cyan"),
                       subtitle=f"{server_url}  ·  {online} online")
        footer = Panel(Text("[i]ncidents [o]perators [d]ash · 1-9 select · "
                            "[c]laim [k]ack [x]release · [q]uit\n" + (state["status"] or " "),
                            style="dim"))
        return Group(header, bodies.get(state["view"], body_incidents)(), footer)

    for fn in (heartbeat, poll, keys):
        threading.Thread(target=fn, daemon=True).start()
    with Live(render(), screen=True, auto_refresh=False) as live:
        while not state["quit"]:
            if state["sel"] >= len(state["incidents"]):
                state["sel"] = max(0, len(state["incidents"]) - 1)
            live.update(render())
            live.refresh()
            time.sleep(0.5)


# ── command console (non-LLM operator interface) ──────────────────────────────
_CONSOLE_HELP = """commands:
  selftest                      check every integration
  discover <cidr> [cidr...]     scan networks, generate targets/monitors
  discover-logs <host> <admin>  preview a host's log files
  logs <host> <path> [pattern]  read an allow-listed log (read-only)
  jira <query>                  search Jira for prior incidents
  kb <query>                    search Confluence runbooks
  report [since]                shift report (default 8h)
  incidents                     list firing incidents          (needs server)
  operators                     list online operators          (needs server)
  claim|ack|release <id>        act on an incident (id prefix ok, needs server)
  help                          this list
  exit                          quit
"""


def console():
    """Plain command interface to nanny's operations — no LLM, just commands."""
    server_url = os.environ.get("NANNY_SERVER_URL")
    session = {"op_id": None}

    def need_server():
        if not server_url:
            print("set NANNY_SERVER_URL to use server commands")
            return None
        if not session["op_id"]:
            try:
                session["op_id"] = _panel_login(server_url, os.environ.get("NANNY_OPERATOR", "console"))
            except SystemExit as e:
                print(str(e))
                session["op_id"] = None
        return session["op_id"]

    def safe(fn, *a):
        try:
            fn(*a)
        except SystemExit:           # the underlying ops sys.exit on bad input
            pass

    def c_discover(a):
        if not a:
            return print("usage: discover <cidr> [cidr...]")
        discover(a, DEFAULT_SCAN_PORTS, "inventory.json", "monitors.json", "prom_targets.json")

    def c_logs(a):
        if len(a) < 2:
            return print("usage: logs <host> <path> [pattern]")
        print(fetch_logs(a[0], a[1], a[2] if len(a) > 2 else None))

    def c_incidents(a):
        op = need_server()
        if not op:
            return
        _, res = _panel_api("GET", server_url, "/api/state", token=op)
        rows = res.get("incidents", [])
        for i in rows:
            print(f"  {i['id']}  {i['severity']:<8}  {i['summary'][:48]:<48}  "
                  f"owner={i['owner'] or '-'}  ack={'Y' if i['acked'] else 'N'}")
        if not rows:
            print("  (nothing firing)")

    def c_operators(a):
        op = need_server()
        if not op:
            return
        _, res = _panel_api("GET", server_url, "/api/state", token=op)
        for o in res.get("operators", []):
            print(f"  {o['name']}  (idle {o['idle']}s)")
        if not res.get("operators"):
            print("  (none online)")

    def action(name):
        def f(a):
            op = need_server()
            if not op:
                return
            if not a:
                return print(f"usage: {name} <incident_id>  (prefix ok)")
            _, st = _panel_api("GET", server_url, "/api/state", token=op)
            ids = [i["id"] for i in st.get("incidents", []) if i["id"].startswith(a[0])]
            if len(ids) != 1:
                return print("no match" if not ids else "ambiguous prefix")
            payload = {}
            if name == "ack":
                payload["idem"] = uuid.uuid4().hex
            code, res = _panel_api("POST", server_url,
                                   f"/api/incidents/{quote(ids[0], safe='')}/{name}",
                                   payload, token=op)
            print(f"{name}: {res}" if code == 200 else f"{name} failed ({code}): {res}")
        return f

    commands = {
        "help": lambda a: print(_CONSOLE_HELP), "?": lambda a: print(_CONSOLE_HELP),
        "selftest": lambda a: safe(selftest),
        "discover": c_discover,
        "discover-logs": lambda a: print("usage: discover-logs <host> <admin>")
            if len(a) < 2 else safe(discover_logs, a[0], a[1]),
        "logs": c_logs,
        "jira": lambda a: print(search_jira(" ".join(a))) if a else print("usage: jira <query>"),
        "kb": lambda a: print(search_confluence(" ".join(a))) if a else print("usage: kb <query>"),
        "confluence": lambda a: print(search_confluence(" ".join(a))) if a else print("usage: kb <query>"),
        "report": lambda a: safe(report, a[0] if a else "8h"),
        "incidents": c_incidents, "operators": c_operators,
        "claim": action("claim"), "ack": action("ack"), "release": action("release"),
    }

    print(_c("nanny console", "1;36") + " — type 'help' for commands, 'exit' to quit")
    while True:
        try:
            line = input(_c("nanny> ", "1;36")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        try:
            parts = shlex.split(line)
        except ValueError:
            print("unbalanced quotes")
            continue
        cmd, args = parts[0], parts[1:]
        if cmd in ("exit", "quit"):
            break
        fn = commands.get(cmd)
        if not fn:
            print(f"unknown command: {cmd} (try 'help')")
            continue
        try:
            fn(args)
        except Exception as e:
            print(f"error: {e}")


# ── entrypoint ────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="nanny — supervised alert triage bot")
    sub = parser.add_subparsers(dest="mode", required=True)

    sub.add_parser("run", help="watch Slack and triage incoming alerts")
    sub.add_parser("console", help="plain command interface to nanny's operations (no LLM)")
    sub.add_parser("shell", help="LLM reasoning REPL (talk to the triage agent)")
    sub.add_parser("tui", help="live monitoring console: dashboard / fleet / incidents / reports")
    sub.add_parser("selftest", help="probe every integration and report reachability")

    p = sub.add_parser("install", help="provision the read-only log account on a host")
    p.add_argument("--host", required=True, help="target host")
    p.add_argument("--admin-user", required=True, help="sudo-capable account you SSH in as")
    p.add_argument("--pubkey", required=True, help="path to the bot's SSH public key")
    p.add_argument("--logs", help="comma-separated log paths (skips discovery)")
    p.add_argument("--no-discover-logs", dest="discover_logs", action="store_false",
                   help="don't auto-discover logs; use --logs or the in-code seed")
    p.add_argument("--dry-run", action="store_true", help="print the provisioning script, don't run it")

    dl = sub.add_parser("discover-logs", help="preview the log files nanny would monitor on a host")
    dl.add_argument("--host", required=True, help="target host")
    dl.add_argument("--admin-user", required=True, help="sudo-capable account you SSH in as")

    u = sub.add_parser("uninstall", help="remove the read-only log account from a host")
    u.add_argument("--host", required=True, help="target host")
    u.add_argument("--admin-user", required=True, help="sudo-capable account you SSH in as")
    u.add_argument("--dry-run", action="store_true", help="print the removal script, don't run it")

    dsc = sub.add_parser("discover", help="inventory the fleet from Icinga (or a network scan) and generate monitors")
    dsc.add_argument("--source", choices=["icinga", "cidr"],
                     help="where to inventory from (default: icinga if ICINGA_API_URL is set, else cidr)")
    dsc.add_argument("--cidr", action="append",
                     help="network to scan for --source cidr, e.g. 10.0.1.0/24 (repeatable; only networks you operate)")
    dsc.add_argument("--ports", help="comma-separated ports (default: common service ports)")
    dsc.add_argument("--out", help="inventory output (default: $NANNY_DATA_DIR/inventory.json)")
    dsc.add_argument("--monitors", help="monitors output (default: $NANNY_DATA_DIR/monitors.json)")
    dsc.add_argument("--prom-targets",
                     help="blackbox targets output (default: $NANNY_TARGETS_DIR/discovered.json)")

    mon = sub.add_parser("monitor", help="check all monitors with retry backoff + escalation")
    mon.add_argument("--monitors", default="monitors.json", help="monitors config to run")

    sub.add_parser("webhook", help="alias for `server` (back-compat)")
    sub.add_parser("server", help="always-on backend: Alertmanager webhook + operator API")
    sub.add_parser("demo", help="self-contained demo: synthetic incidents + ntfy, no real integrations")
    pn = sub.add_parser("panel", help="operator control panel (connects to the server)")
    pn.add_argument("--server", help="server URL, e.g. http://nanny-host:9099 (or NANNY_SERVER_URL)")
    pn.add_argument("--operator", help="your display name (or NANNY_OPERATOR)")

    rep = sub.add_parser("report", help="generate a shift report from nanny's incident log")
    rep.add_argument("--since", default="8h", help="window: e.g. 90m, 8h, 1d")

    args = parser.parse_args()
    if args.mode == "run":
        run()
    elif args.mode == "shell":
        shell()
    elif args.mode == "console":
        console()
    elif args.mode == "tui":
        tui()
    elif args.mode == "selftest":
        selftest()
    elif args.mode == "install":
        manual = args.logs.split(",") if args.logs else None
        install(args.host, args.admin_user, args.pubkey, args.dry_run,
                manual, args.discover_logs)
    elif args.mode == "discover-logs":
        discover_logs(args.host, args.admin_user)
    elif args.mode == "uninstall":
        uninstall(args.host, args.admin_user, args.dry_run)
    elif args.mode == "discover":
        source = args.source or ("icinga" if _icinga_configured() else "cidr")
        if source == "icinga":
            if not _icinga_configured():
                sys.exit("discover --source icinga: set ICINGA_API_URL (+ ICINGA_API_USER/"
                         "ICINGA_API_PASSWORD) first.")
            discover_icinga(args.out, args.monitors, args.prom_targets)
        else:
            if not args.cidr:
                sys.exit("discover --source cidr: pass at least one --cidr to scan.")
            scan_ports = [int(p) for p in args.ports.split(",")] if args.ports else DEFAULT_SCAN_PORTS
            discover(args.cidr, scan_ports, args.out, args.monitors, args.prom_targets)
    elif args.mode == "monitor":
        monitor(args.monitors)
    elif args.mode in ("server", "webhook"):
        server()
    elif args.mode == "demo":
        demo()
    elif args.mode == "panel":
        panel(args.server, args.operator)
    elif args.mode == "report":
        report(args.since)


if __name__ == "__main__":
    main()
