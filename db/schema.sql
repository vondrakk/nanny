-- nanny durable state — CloudNativePG-backed Postgres.
-- All shared state lives here so the web/brain pods stay stateless and can scale
-- horizontally. Applied idempotently at startup (CREATE ... IF NOT EXISTS).

-- Operator sessions (the bearer token is the PK). Expiry is enforced in the app
-- against created/last_seen, and a periodic sweep deletes dead rows.
CREATE TABLE IF NOT EXISTS sessions (
  token      TEXT PRIMARY KEY,
  name       TEXT NOT NULL,
  created    TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS sessions_last_seen ON sessions (last_seen);

-- Active incidents. `page_at` + `paged` drive the stateless paging reconciler:
-- any replica may claim due, un-acked, un-owned incidents with FOR UPDATE SKIP
-- LOCKED and page them exactly once. Resolved incidents are moved to incident_log.
CREATE TABLE IF NOT EXISTS incidents (
  fingerprint TEXT PRIMARY KEY,
  status      TEXT NOT NULL DEFAULT 'firing',     -- firing | resolved
  summary     TEXT,
  sig         TEXT,
  severity    TEXT,
  instance    TEXT,
  service     TEXT,
  phase       TEXT NOT NULL DEFAULT 'triaging',   -- triaging | holding | claimed | paged
  hold_secs   INTEGER NOT NULL DEFAULT 0,
  page_at     TIMESTAMPTZ,                         -- when to page if still firing & unclaimed
  paged       BOOLEAN NOT NULL DEFAULT false,
  owner       TEXT,
  owner_name  TEXT,
  acked       BOOLEAN NOT NULL DEFAULT false,
  ack_idem    JSONB NOT NULL DEFAULT '{}',         -- idempotency keys → cached ack result
  ticket      TEXT,
  runbook     TEXT,
  notes       TEXT,
  analysis    TEXT,
  audit       JSONB NOT NULL DEFAULT '[]',
  descr       TEXT,                                -- 'desc' is a reserved word
  slack_ts    TEXT,                                -- Slack card message ts, for in-place updates
  started     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated     TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Partial index for the reconciler's hot path (due, not-yet-paged, firing).
CREATE INDEX IF NOT EXISTS incidents_page_due
  ON incidents (page_at) WHERE status = 'firing' AND paged = false;
CREATE INDEX IF NOT EXISTS incidents_firing
  ON incidents (status) WHERE status = 'firing';

-- Self-resolve history per alert signature → drives the adaptive hold.
CREATE TABLE IF NOT EXISTS history (
  sig      TEXT PRIMARY KEY,
  samples  JSONB NOT NULL DEFAULT '[]'             -- [{self_resolved: bool, duration: int}], last 50
);

-- Resolved-incident log (formerly incidents.jsonl) — feeds reports/MTTR.
CREATE TABLE IF NOT EXISTS incident_log (
  id            BIGSERIAL PRIMARY KEY,
  ended         TIMESTAMPTZ NOT NULL DEFAULT now(),
  sig           TEXT,
  alertname     TEXT,
  service       TEXT,
  instance      TEXT,
  ticket        TEXT,
  paged         BOOLEAN,
  self_resolved BOOLEAN,
  closed        BOOLEAN,
  duration      INTEGER
);
CREATE INDEX IF NOT EXISTS incident_log_ended ON incident_log (ended);

-- Activity feed + tool-use AUDIT trail (durable; addresses audit F11). The console
-- polls by id > since; a retention job trims old rows.
CREATE TABLE IF NOT EXISTS activity_log (
  id     BIGSERIAL PRIMARY KEY,
  ts     TIMESTAMPTZ NOT NULL DEFAULT now(),
  kind   TEXT NOT NULL,
  text   TEXT NOT NULL,
  extra  JSONB NOT NULL DEFAULT '{}'
);

-- On-demand jobs (discovery refresh, etc.) — shared so any pod can report status.
-- started/finished are display strings (HH:MM:SS) the app stamps, kept identical
-- across both store backends.
CREATE TABLE IF NOT EXISTS jobs (
  id       TEXT PRIMARY KEY,
  kind     TEXT,
  status   TEXT,                                   -- running | done | failed
  started  TEXT,
  finished TEXT,
  summary  TEXT,
  error    TEXT
);
