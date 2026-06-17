# nanny — self-contained demo

Run nanny end-to-end with **no real integrations** — no Slack, no PagerDuty, no Jira,
no Icinga, no API keys. A background scenario injects synthetic incidents on a loop and
drives the full lifecycle:

```
discover ──▶ triage ──▶ adaptive hold ──▶ page ──▶ resolve ──▶ report
```

Notifications — including the **page** — go to [ntfy](https://ntfy.sh), a free,
no-account push service you subscribe to from your phone or a browser. Side effects run
in dry-run (tickets become fake `DRY-xxxx` ids), and triage uses the offline no-LLM path,
so the demo costs nothing and leaks nothing.

## What you'll see

Every ~30s the scenario runs one pass:

| t      | event                                                        | channel                |
|--------|--------------------------------------------------------------|------------------------|
| +0s    | `web-01/api-5xx` fires → triage opens a (dry) ticket         | console + ntfy (info)  |
| +5s    | `db-02/replication-lag` fires → triage, enters a 12s hold    | console + ntfy (info)  |
| +15s   | `web-01` recovers **before its hold** → self-resolved, no page | ntfy (recovered)     |
| ~+17s  | `db-02` hold elapses → **on-call is paged**                  | ntfy **(urgent page)** |
| +33s   | `db-02` recovers after paging                                | ntfy (recovered)      |

The point: the alert that clears itself never wakes anyone; only the one that persists
past its adaptive hold pages.

## Quick start

### 1. Subscribe to a topic

Pick a **unique, hard-to-guess topic name** (on public ntfy.sh anyone who knows the
topic can read it), e.g. `nanny-demo-7gk2qz`. Then either:

- **Phone:** install the *ntfy* app (iOS/Android) → **+** → subscribe to your topic, or
- **Browser:** open `https://ntfy.sh/<your-topic>`.

### 2. Start nanny

With **docker compose** / **podman-compose**:

```bash
cd deploy/demo
NANNY_NTFY_TOPIC=nanny-demo-7gk2qz docker compose up --build
```

Or a single container, no compose:

```bash
podman build -t nanny:demo -f Containerfile .       # from the repo root
podman run --rm -p 9099:9099 \
  -e NANNY_NTFY_TOPIC=nanny-demo-7gk2qz \
  -e NANNY_DATA_DIR=/tmp/nanny \
  nanny:demo demo
```

(If you omit `NANNY_NTFY_TOPIC`, nanny generates one and prints it on startup.)

### 3. Watch it

- **Console:** <http://localhost:9099> — log in with **any name** and password **`demo`**.
  The Fleet tab shows a fabricated fleet (the firing host turns red), and the Triage tab
  shows nanny investigating, holding, and paging in real time.
- **ntfy:** your phone/browser buzzes as incidents fire, page, and recover.

## Interactive pages (tap to Ack / Claim)

By default the ntfy pages are informational. To make the page's **Ack** / **Claim**
buttons drive nanny from your phone, set `NANNY_PUBLIC_URL` to a URL **your phone can
reach nanny at** — your laptop's LAN IP or a tunnel, not `localhost`:

```bash
NANNY_NTFY_TOPIC=nanny-demo-7gk2qz \
NANNY_PUBLIC_URL=http://192.168.1.50:9099 \
docker compose up --build
```

> The ntfy app runs the button's HTTP call from the phone, so the phone — not ntfy.sh —
> must be able to reach nanny. Same Wi-Fi is the simplest setup. The button authenticates
> with a demo token nanny pre-seeds as a session, so the tap claims/acks the real incident.

## Configuration

| Variable               | Default            | Purpose                                                        |
|------------------------|--------------------|----------------------------------------------------------------|
| `NANNY_NTFY_TOPIC`     | *(auto-generated)* | ntfy topic to publish to. **Set a unique one.**                |
| `NANNY_NTFY_URL`       | `https://ntfy.sh`  | ntfy server. Point at a self-hosted server to stay fully offline. |
| `NANNY_NTFY_TOKEN`     | *(none)*           | Bearer token, only for access-controlled ntfy topics.          |
| `NANNY_PUBLIC_URL`     | *(none)*           | URL the phone reaches nanny at → enables Ack/Claim buttons.     |
| `NANNY_AUTH_PASSWORD`  | `demo`             | Console login password (any username).                         |
| `NANNY_DEMO_SCENARIO`  | `true`             | Set `false` to disable the auto-driver and inject manually.    |
| `NANNY_DEMO_SPEED`     | `1`                | Pace multiplier for the fire→hold→page→resolve arc. `3` ≈ 100s (relaxed, demo-friendly); `<1` faster. Clamped 0.25–12. |
| `NANNY_DEMO_LOOP_PAUSE`| `30`               | Seconds between scenario passes.                               |

`demo` mode is just `server` mode with safe defaults pre-set — every default above is
overridable, and the normal console, webhooks, and `/api/test/page` injection all still work.

## Fully offline (no internet)

Uncomment the `ntfy` service in `compose.yaml` to run your own ntfy server, set
`NANNY_NTFY_URL=http://ntfy`, and point the ntfy app at `http://<host>:8080`. Nothing
then leaves your network — the [official `binwiederhier/ntfy`](https://hub.docker.com/r/binwiederhier/ntfy)
image is the server.

## How it works (under the hood)

- **No real side effects:** demo mode sets `NANNY_DRY_RUN=true`, so Jira/PagerDuty/Slack
  calls are intercepted and logged. ntfy is the exception — it fires anyway, because it's
  the safe demo channel (no live on-call is woken).
- **Offline triage:** `NANNY_LLM_BACKEND=none` gathers evidence deterministically (no
  tokens, no cost). Set `NANNY_LLM_BACKEND=anthropic` + `ANTHROPIC_API_KEY` to swap in
  real Claude triage.
- **Exactly-once paging:** the page is driven by the same stateless reconciler used in
  production (`page_at` + atomic claim), not a demo shortcut — so the demo exercises the
  real paging path.
- **The channel:** see [`nanny/ntfy.py`](../../nanny/ntfy.py). It's wired into `notify()`
  for status pushes; the page is sent from `_page_incident` so it can carry action buttons.
