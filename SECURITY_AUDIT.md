# nanny — OWASP Top 10 (2021) + SOC 2 Security Audit

**Scope:** `nanny.py` + `Containerfile.bundle`, `bundle/entrypoint.sh`, `bundle/supervisord.conf`, `nanny.env(.example)`, `requirements.txt`.
**Frameworks:** OWASP Top 10 (2021), SOC 2 Trust Services Criteria.
**Date:** 2026-06-06. **Method:** static review (code not executed during the audit).

> ⚠️ **Line numbers** below reference the revision audited on 2026-06-06. Subsequent
> feature work (triage view, Icinga triage tool, spinner) shifted some lines; the
> findings still hold, but re-grep before quoting a line in a ticket.

---

## Remediation status (updated as fixes land)

| Finding | Status | Note |
|---|---|---|
| **F2 — weak/non-expiring token in URL** | ✅ **Fixed** | Token is now `secrets.token_urlsafe(32)` (256-bit), sent only in `Authorization: Bearer` (rejected in URL), with 30-min idle + 12-h absolute expiry and `/api/logout` invalidation. Web + CLI clients updated. |
| **F3 — endpoints unauthenticated when LDAP off** | ✅ **Fixed** | Auth decoupled from LDAP: modes `ldap`/`password` (`NANNY_AUTH_PASSWORD`)/`open` (only with `NANNY_AUTH_INSECURE=true`, else logins refused). A valid session is now required on **every** `/api/*` except `config`/`login`/`logout`/`healthz`/console — reads included. |
| **F4 — unauthenticated webhooks** | ✅ **Fixed** | `/alert` + `/icinga` now require `NANNY_WEBHOOK_TOKEN`: HMAC-SHA256 signature (`X-Nanny-Signature`) or `Authorization: Bearer` / `X-Nanny-Token`, constant-time. Unset → open with a loud startup warning. |
| **F5 — no login rate-limit** | ✅ **Fixed** | Per-IP throttle: 5 failures / 5 min → 429 lockout; cleared on success; config-not-configured not counted as a failure. |
| F9 — unbounded request body | ✅ **Fixed (partial)** | `_body()` now rejects bodies > `MAX_BODY_BYTES` (256 KB) with 413. Thread-pool bounding still open. |
| F8 — `notes_url` `javascript:` XSS | ✅ **Fixed** | `safeUrl()` (http(s)-only) for all `notes_url`/ticket hrefs + `rel="noopener noreferrer"`; `esc()` now also escapes `'`. |
| New triage Icinga tool (`get_icinga_state`) | ✅ Built safe | Uses Icinga `filter_vars` (parameterised) — no filter injection via the alert's host field. |
| New `/api/incidents/<id>` detail endpoint | ✅ Auth-gated | Behind the unified session gate. |
| F1 — exposed secrets in `nanny.env` | ✅ **Resolved by owner** | Secrets rotated and moved out of the world-readable env file. |
| Remaining (F6, F7, F10–F15) | ⏳ Open | **Top priority: F6 (TLS verify), F7 (CSRF), F10 (prompt-injection delimiting), F11 (durable audit log).** |

---

## Severity-ranked summary

| # | Severity | Title | OWASP / SOC 2 |
|---|----------|-------|---------------|
| F2 | **Critical** | Session token (`operator_id`) is 32-bit, guessable, never expires, passed in URL query strings | A07 / A01 · CC6.1 |
| F3 | **Critical** | All mutating + read endpoints are unauthenticated when LDAP is off (the default) | A01 / A07 · CC6.1, CC6.3 |
| F4 | **High** | Unauthenticated webhooks `/alert` and `/icinga` let any network peer forge incidents, open Jira tickets, page PagerDuty, inject LLM prompts | A01 / A04 · CC6.6, CC7.1 |
| F5 | **High** | No rate limiting / lockout on `/api/login`; brute-forceable LDAP bind and session enumeration | A07 · CC6.1 |
| F6 | **High** | TLS verification disablement: `ICINGA_INSECURE` (CERT_NONE), `ICINGA_LEGACY_TLS` (TLSv1+SECLEVEL 0); `discover_icinga` probe hardcodes `verify=False` | A02 / A05 · CC6.1, CC6.7 |
| F7 | **High** | No CSRF protection on JSON mutating endpoints | A01 · CC6.6 |
| F8 | **Medium** | Stored XSS via `notes_url` rendered into `href` without scheme filtering | A03 · CC6.6 — ✅ **FIXED** |
| F9 | **Medium** | Unbounded request body (`Content-Length` trusted); thread-per-alert/request DoS | A05 / A04 · CC7.1, A1.1 |
| F10 | **Medium** | LLM prompt injection via attacker-controlled alert fields drives tool use | A03 / A04 · CC6.6 |
| F11 | **Medium** | Audit trail in-memory only; `log_audit` is a TODO that only prints — no tamper-resistant who-did-what | A09 · CC7.2, CC7.3 |
| F12 | **Low** | Error strings can surface upstream token-bearing response bodies | A09 · CC7.2 |
| F13 | **Low** | Weak CQL/JQL escaping (`_quote_escape` only escapes `"`) | A03 · CC6.6 |
| F14 | **Low/Info** | Dependencies floor-pinned (`>=`), no hashes/lockfile; base image + Go binaries not digest/checksum-pinned | A06 / A08 · CC8.1 |
| F15 | **Info** | Operator-triggerable discovery scan = SSRF/recon pivot (capped at 8192 hosts) | A04 · CC7.1 |

**Done right (not overstating risk):** the SSH log-reader subsystem is genuinely well-defended (forced-command wrapper, no shell, `shlex.quote` client-side + `shlex.split`/allowlist re-check host-side, `grep -F --`, stripped key capabilities — **no injection path found**); the container drops root to uid 10001 reliably via `gosu`; LDAP filter escaping (`_ldap_esc`) is correct; HTML is escaped through `esc()` almost everywhere.

---

## Findings (detail)

> **F1 (exposed secrets in `nanny.env`) — resolved by the owner** (secrets rotated and removed from the world-readable env file) and dropped from this audit at their request.

### F2 — Weak, non-expiring session token in URL query strings (Critical)
**A07/A01 · CC6.1.** `_op_login` mints `uuid.uuid4().hex[:8]` — **32 bits**, not a UUID — as the session token; it rides in the query string on every GET (`/api/state?operator_id=...`), landing in proxy/LB logs, browser history, `Referer`. `_operators` entries **never expire** (last_seen only governs the "online" list). Guessable + unthrottled (F5) + no expiry.
**Fix:** ≥128-bit `secrets.token_urlsafe(32)`; transmit in `Authorization` header or `HttpOnly; Secure; SameSite=Strict` cookie, never the URL; absolute + idle expiry; invalidate on logout; constant-time compare.

### F3 — Endpoints unauthenticated when LDAP is off (default) (Critical)
**A01/A07 · CC6.1, CC6.3.** Auth engages only if `NANNY_LDAP_URL` is set. Default: `/api/login` mints a session for **any name, no password**, and the `_known_operator` guards are no-ops, so `/api/state`, `/api/log`, `/api/fleet`, `/api/incidents/<id>/claim|ack|release`, `/api/actions/discover` are reachable by anyone who can hit :9099. Even with LDAP on, the GET read endpoints `/api/config`, `/api/integrations`, `/api/fleet`, `/api/reports`, `/api/series`, `/api/jobs` have **no auth check**.
**Fix:** require auth unconditionally (decouple from LDAP presence; fail closed or use a configured local credential); apply the guard to *all* `/api/*` incl. reads; add roles (operator vs viewer); bind the listener to an internal interface by default rather than `("", port)`.

### F4 — Unauthenticated webhooks → forged incidents, ticket/page spam, prompt injection (High)
**A01/A04 · CC6.6, CC7.1.** Any host that reaches :9099 can POST a crafted alert to `/alert` or `/icinga`; `handle_firing` will open a real Jira ticket, run the LLM on attacker text, and after the hold trigger a real PagerDuty page — no shared secret, HMAC, or source allowlist. Enables ticket spam, on-call alert-fatigue/DoS, and malicious content seeded into tickets/Slack.
**Fix:** HMAC/bearer/mTLS on webhooks, validated before dispatch; rate-limit per source; bind webhook intake to an internal interface separate from the console.

### F5 — No rate limiting / lockout on `/api/login` (High)
**A07 · CC6.1.** With LDAP on, each login does a live LDAP **bind**; with no throttle/lockout nanny is an unauthenticated online password-guessing oracle against corporate AD/LDAP (and can trip account lockout). With LDAP off, F2 token-guessing is similarly unthrottled.
**Fix:** per-IP and per-username rate limit + backoff; temporary lockout; alert on `AUTH`-denial bursts (already logged).

### F6 — TLS verification disabled / downgraded for Icinga (High)
**A02/A05 · CC6.1, CC6.7.** `ICINGA_INSECURE` → `CERT_NONE` (MITM: forge host/service state, inject `notes_url`/`output` into the console); `ICINGA_LEGACY_TLS` lowers to TLSv1 + OpenSSL SECLEVEL 0 (broken ciphers; exposes the Basic-auth `ICINGA_API_PASSWORD`); `discover_icinga`'s HTTP probe hardcodes `verify=False` **regardless of flags**.
**Fix:** default to full verification; pin `ICINGA_API_CA` instead of `INSECURE`; remove the hardcoded `verify=False`; keep LEGACY_TLS ≥ TLS1.2; never combine SECLEVEL drop with `CERT_NONE`; log loudly when verification is off.

### F7 — No CSRF protection on mutating endpoints (High)
**A01 · CC6.6.** Mutating POSTs take `operator_id` in the body with no CSRF token, `Origin`/`Referer` check, or enforced custom header. Becomes exploitable if the token ever moves to a cookie (F2 fix) or an op_id is guessed/leaked.
**Fix:** CSRF token or strict `Origin`/`Referer` allowlist + a preflight-forcing custom header on all state changes; `SameSite=Strict` on any cookie.

### F8 — Stored XSS via `notes_url` href (Medium) — ✅ FIXED
**A03 · CC6.6.** `notes_url` (from Icinga custom vars, MITM-influenceable under F6) was interpolated into an `href` after only `esc()`, which doesn't block the URL scheme — `javascript:...` would execute in the authenticated console origin (read `OP`, drive any API). **Fixed:** added `safeUrl()` (allows only `http(s):`), applied to all `notes_url`/ticket links, added `rel="noopener noreferrer"`, and extended `esc()` to escape `'`.

### F9 — Unbounded request body; thread-per-request/alert DoS (Medium)
**A05/A04 · CC7.1, A1.1.** `_body()` trusts `Content-Length` and reads/JSON-parses with no cap (memory exhaustion). Each alert/test spawns an unbounded daemon thread; a webhook flood (F4) creates unbounded threads each invoking the LLM + external APIs.
**Fix:** max body size (reject before reading); bound alert processing with a fixed `ThreadPoolExecutor`/queue; global in-flight cap; rate-limit webhook intake.

### F10 — LLM prompt injection via alert fields (Medium)
**A03/A04 · CC6.6.** Alert `alertname/service/summary/description` are concatenated verbatim into the triage prompt and the agent is told to use tools. A crafted `description` can steer the model to surface allowlisted log contents into a ticket the attacker can later read, pollute incident records, or waste LLM budget. (`fetch_logs` allowlist limits exfil; read-only limits damage.)
**Fix:** treat alert content as data, not instructions — delimit it, instruct the model never to follow embedded instructions; authenticate webhooks (F4); keep the durable per-incident audit (F11).

### F11 — Audit trail in-memory and non-durable (Medium)
**A09 · CC7.2, CC7.3.** Activity log is a bounded in-memory `deque` lost on restart; `log_audit` is a TODO that only `print`s (no SIEM/integrity/retention); `incidents.jsonl` is app-writable with no signing; claim/ack record a self-asserted display *name* (unverifiable under name-only mode). Fails CC7.2/7.3 expectations.
**Fix:** ship `log_audit` to an append-only/WORM log or SIEM with timestamp, verified operator identity, source IP; hash-chain/sign `incidents.jsonl`; define retention; alert on auth failures and webhook floods.

### F12 — Upstream response bodies / exceptions surfaced in errors (Low)
**A09 · CC7.2.** PD ack error echoes `r.text[:200]`; `_chk_*` raise raw exceptions; job `error: str(e)[:300]` is returned via `/api/jobs`. Combined with `/api/log` being readable when LDAP is off (F3), operational detail can leak to unauthenticated viewers.
**Fix:** don't propagate raw upstream bodies/exceptions to clients/shared logs; log sanitized messages with a correlation id; mask token-shaped strings.

### F13 — Weak CQL/JQL escaping (Low)
**A03 · CC6.6.** `_quote_escape` only escapes `"` (not `\`), so `\"` can break out of the quoted value; operator/LLM/attacker-influenced (F10) query injection into Confluence/Jira search, bounded by the Atlassian account's read scope.
**Fix:** escape backslash before quote (or use validated/parameterised search); constrain query length/charset.

### F14 — Container / supply-chain (Low/Info)
**A06/A08 · CC8.1.** *Positive:* non-root runtime (uid 10001 via `gosu`), refuses to run if it can't start as root (reliable privilege drop), `tini` PID 1, Go binaries version-pinned + licenses vendored. *Gaps:* Python deps floor-pinned `>=` with no hashes/lockfile (non-reproducible, A08 exposure); Go binaries fetched over HTTPS but not checksum-verified; base `python:3.12-slim` not digest-pinned; verify the `alertmanager 0.32.1` pin resolves to a real release.
**Fix:** pin exact versions + hashes (`pip --require-hashes` w/ lock); pin base image by digest; `sha256sum -c` the Go releases; add SBOM + image scanning to CI.

### F15 — Discovery scan abuse / SSRF surface (Info→Medium)
**A04 · CC7.1.** `/api/actions/discover` lets a (possibly unauthenticated, F3) caller make nanny TCP-connect-scan arbitrary CIDRs — an internal recon/SSRF pivot. Bounded by CIDR validation + 8192-host cap; no exploitation/creds.
**Fix:** require auth+authz (F3); restrict targets to a configured network allowlist; lower the web cap; log/alert/rate-limit.

### F-SSH — fetch_logs / SSH path reviewed: no injection found (Info — positive)
Allowlist checked before SSH; args via `shlex.quote` + ssh arg-list (no shell); host-side forced command ignores client command, re-validates `path` against a baked-in allowlist, uses `grep -F --`. Even a leaked key reads only allowlisted files. Minor theoretical note: a hostname beginning with `-` could be read as an ssh option — guard against leading-dash hostnames. **Leave this subsystem as is.**

---

## SOC 2 readiness summary

**Overall: not ready.** Controls lean almost entirely on an implicit network perimeter, with the app defaulting to no auth and secrets stored insecurely.

- **CC6.1/6.2/6.3 (logical access):** major gaps — auth optional & off by default (F3); weak URL-borne non-expiring tokens (F2); no rate-limit/lockout (F5); reads unauthenticated even with LDAP on (F3); no RBAC; no CSRF (F7).
- **CC6.6 (boundary protection):** gaps — unauthenticated webhooks (F4), console (F3), SSRF scan trigger (F15), XSS (F8, now fixed). Strong point: SSH log-reader.
- **CC6.7 (credential/data protection):** TLS disablement/downgrade for Icinga (F6) remains; the earlier plaintext-secrets gap (F1) has been resolved.
- **CC7.1 (detect/monitor):** gaps — no rate-limit/anomaly alerting; DoS exposure (F9, F15).
- **CC7.2/7.3 (logging & evaluation):** major gaps — audit hook is an unimplemented TODO; logs in-memory/tamperable; self-asserted operator identity; no SIEM (F11); error leakage (F12).
- **CC8.1 (change mgmt/build integrity):** gaps — floor-pinned deps, unverified pins (F14). Positive: non-root, reliable privilege drop.
- **Availability A1.1:** gaps — unbounded bodies, thread-per-alert (F9).

**Top actions toward readiness:** (1) ✅ done — auth mandatory on *every* `/api/*` with strong header tokens + expiry (F2, F3); (2) ✅ done — authenticate + rate-limit the webhooks (F4, F5); (3) durable tamper-evident audit logging with verified identity → SIEM (F11); (4) default TLS verification on; remove the hardcoded `verify=False` (F6); (5) CSRF protection (F7). *(F1 — exposed secrets — has been resolved by the owner.)*
