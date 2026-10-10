---
name: cloudflare-ops
description: >-
  Cloudflare operations: Zero Trust Access paths (SSH/RDP/short-lived certs), API token creation/scoping/rotation, and the D1↔PostgreSQL dual-write sync (free-tier row limits, backfills). Use for any Cloudflare config or D1 sync issue.
---

# Cloudflare Ops

Consolidated skill — each section below was a standalone skill. Member scripts/templates live under `<source-skill>/` inside this directory.

| Section | Source |
|---|---|
| Cloudflare Access — public paths on social.cloudless.gr | `cloudflare-ops` |
| Cloudflare Token Ops | `cloudflare-ops` → `cloudflare-token-ops/` |
| D1 sync operations | `cloudflare-ops` |

## Cloudflare Access — public paths on social.cloudless.gr

`social.cloudless.gr` is behind Cloudflare Access (Zero Trust). App
`socialauto-app` (id `ed95d3d9-2246-4d44-b15c-5c39170b00dd`) protects the
whole hostname — only the 4 admin emails pass.

### Key model

- Access matches the **most specific** application domain first. A path-scoped
  app (`host/path`) takes precedence over the hostname-wide app.
- "Public" means a **separate self-hosted app** on the same hostname scoped to
  the path, with a single `bypass` policy including `Everyone`. An `allow`
  policy with `Everyone` still runs the identity check — it does NOT open the
  path.
- **Service-token auth needs a `non_identity` (Service Auth) policy.** An
  `allow` policy with `any_valid_service_token` in `include` looks right but
  still redirects machine callers to the IdP (`service_token_status:false` in
  the login-redirect meta JWT). The fix (applied 2026-09-23 to `socialauto-app`):
  a second policy `decision: "non_identity"`, `include: [{"any_valid_service_token": {}}]`,
  precedence after the admin allow-list. Verified: with-token → origin 401/200,
  no-token → 302.
- Bypassed paths are fully public — the endpoint must authenticate itself
  (webhook verify token, HMAC signature, OAuth state).

### Current public (bypassed) paths

| Path prefix | App | Purpose |
|---|---|---|
| `/api/v1/health` | `socialauto-public` | monitoring |
| `/api/v1/auth/oauth/` | `socialauto-oauth-callbacks` (`e5dd7e52-ee37-449f-a03d-3de9fcb10248`) | OAuth callbacks + authorize for all platforms |
| `/api/v1/auth/data-deletion` | `socialauto-meta-data-deletion` (`e3f73ec9-d05a-44e4-bc44-dbc205b71a91`) | Meta data-deletion/deauthorize POSTs |
| `/api/v1/messenger/webhook` | (existing) | Meta Messenger verify+events |
| `/api/v1/whatsapp/webhook` | (existing) | Meta WhatsApp verify+events |
| `/api/v1/telegram/webhook/` | (existing) | Telegram webhooks |
| `/api/v1/billing/*` webhooks | (existing) | Polar/Dodo payment webhooks |
| `/api/v1/media/view` | `socialauto-media-view` (`5cc45c62-a067-4be4-a9ac-89be5396c0bf`) | Public media serving for platform fetches (TikTok `PULL_FROM_URL`, Meta `image_url`). Endpoint is unauthenticated by design — required on the TikTok-verified `cloudless.gr` domain. |

### Credentials

- `CLOUDFLARE_ACCESS_TOKEN` in `.env` — account token `cloudless-access`
  (id `87bc6879…`), the only token that can read/write Access apps (account
  `fb7dc7b69b662480cd5961a4d1913c78`). It fails `user/tokens/verify` but works
  on `/access/*` endpoints. Holds `Account API Tokens Write` — it can mint
  purpose-scoped ACCOUNT tokens on demand (ephemeral-minter pattern; see
  `cloudflare-ops` skill). Lacks `Access: Service Tokens` itself.
- `CLOUDFLARE_API_TOKEN` — works for zone basics (zones list) but NOT Access.
- `CLOUDFLARE_GLOBAL_KEY` + `CLOUDFLARE_EMAIL` (optional, `.env`) — the account
  Global API Key. Only needed for USER-scope token ops (`/user/tokens`);
  account-scope token ops work via `Account API Tokens Write` without it.
  Token values are written to `~/.cache/cf-ops/*.json` (0600), never printed
  — **not /tmp**: this WSL box wipes /tmp between sessions.

`scripts/cf_tokens.py` — token management:

```bash
python3 scripts/cf_tokens.py verify                     # auth mode + key check
python3 scripts/cf_tokens.py list                       # user + account tokens
python3 scripts/cf_tokens.py perm-groups --scope account service  # find group ids
python3 scripts/cf_tokens.py add-perm <token-id> "Access: Service Tokens"
python3 scripts/cf_tokens.py create my-token --scope account --perm "Workers Scripts"
python3 scripts/cf_tokens.py service-token cloudless-site-bridge --duration forever
```

### Tools

`scripts/cf_access.py` (repo-root `scripts/`) — list apps, probe paths, create
bypass apps, and toggle review mode:

```bash
## List all Access apps (name | domain | decision)
python3 scripts/cf_access.py list

## Probe which paths reach origin vs Access wall
python3 scripts/cf_access.py probe /api/v1/messenger/webhook /api/v1/auth/data-deletion

## Create a bypass app for a path prefix
python3 scripts/cf_access.py bypass social.cloudless.gr/api/v1/example/webhook --name my-bypass

## App-review mode: temporarily open the WHOLE hostname for platform reviewers
python3 scripts/cf_access.py review-mode status   # check
python3 scripts/cf_access.py review-mode on       # prepend bypass-Everyone policy
python3 scripts/cf_access.py review-mode off      # remove it — restores admin gate
```

### Review mode (whole-hostname bypass)

Meta/TikTok app reviewers must reach `social.cloudless.gr` directly — an
Access SSO wall reads as "login page" and gets the submission rejected (this
exact failure rejected the TikTok review once). `review-mode` does NOT create
a second app on the same domain (ambiguous precedence); it **prepends a
`bypass`/`Everyone` policy named `app-review-temp-bypass` to the existing
`socialauto-app`** (`ed95d3d9-…`). Policy order decides evaluation, and
deleting the temp policy restores the admin allow-list untouched — fully
reversible. While active, exposure = the SocialAuto login page only (the
app's own auth still gates everything). Pair with a scoped reviewer account
(`meta-app-review/scripts/reviewer_account.py`) rather than sharing admin
credentials. Enable right before submitting for review, disable after
approval — do not leave it on.


### How to verify reachability

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://social.cloudless.gr/<path>
```

- `302` to `cloudflareaccess.com` → Access-gated (browser would be redirected to SSO)
- `403` with **no redirect** → origin responded OR Access API-style deny — check
  with a known-good request (e.g. correct `hub.verify_token` for Meta webhooks)
- `4xx/5xx` app-shaped response (400/404/405/422) → reached origin ✓

### Gotchas

- Meta OAuth redirect URIs are registered against `social.cloudless.gr` — if the
  callback path isn't bypassed, the user's browser hits the Access login wall
  mid-flow. The `code` is single-use; the flow must be restarted.
- Meta data-deletion is POSTed server-to-server — no browser, no cookies. Must
  be bypassed or Meta's config check fails.
- Access returns `302` for browser-ish GETs and `403` for API-style requests on
  gated paths — both mean "blocked".
- Do NOT bypass `/api/v1/` broadly — admin endpoints must stay behind Access.
  Bypass only paths that third parties must reach (callbacks, webhooks) or that
  are explicitly public (health).

### Cloudflare MCP servers (registered 2026-09-23)

`.devin/mcp_config.local.json` carries `cloudflare-api` — the official Code
Mode server at `https://mcp.cloudflare.com/mcp`, authenticated with
`CLOUDFLARE_ACCESS_TOKEN` as a Bearer token (no OAuth needed; token needs
`user:read`/`account:read` minimum — `CLOUDFLARE_API_TOKEN` fails that check).
Tools: `docs`, `search` (OpenAPI spec), `execute` (arbitrary CF API calls).
`.devin/mcp_config.json` has `cloudflare-docs` (`https://docs.mcp.cloudflare.com/mcp`,
no auth).

Both are wired as **stdio** servers through `mcp-remote`
(`node ~/.local/lib/mcp-remote/node_modules/mcp-remote/dist/proxy.js <url>
[--header 'Authorization: Bearer …']`) — installed 2026-09-23 via `pnpm add`
in `~/.local/lib/mcp-remote` because `npm`/`npx` shims and `pnpm add -g` are
broken on this box (`pnpm dlx` works). Remote `url`-type entries show yellow
in the client; the stdio bridge is the reliable transport.

Gotchas calling it directly over HTTP (no MCP client):

- `mcp.cloudflare.com` 1010-bot-blocks Python `urllib` — use `curl`.
- Responses are SSE-framed — parse the last `data:` line.
- The server proxies the same API token — it does NOT widen permissions.
- **Empty `GET …/access/service_tokens` is ambiguous**: a caller without
  `Access: Service Tokens Read` gets HTTP 200 `count:0` even when tokens
  exist — this caused a false "wrong account" diagnosis on 2026-09-23.
  Check the calling token's perm groups (`GET /accounts/{acct}/tokens`)
  before concluding absence.
- `cloudless-site-bridge` service token (id `4f790719-…`, `forever`) is live
  and works; an older same-named token `99c39000-…` also exists (both pass
  via `any_valid_service_token`). Bridge creds are in D1 `app_config` as
  `SOCIALAUTO_SERVICE_TOKEN` (`client_id:client_secret`).

## Cloudflare Token Ops

Manage Cloudflare API tokens and Access service tokens programmatically for
the cloudless.gr account (`fb7dc7b69b662480cd5961a4d1913c78`).

### When to use

- Adding a permission group to an existing API token
- Creating user-scoped or account-scoped API tokens
- Creating/listing Cloudflare Access service tokens
- Checking which CF credentials are configured and working

### Auth model (verified 2026-09-23)

- **Account-scope token management works with a scoped token.** An account
  token holding `Account API Tokens Write` (e.g. `cloudless-access`) can
  list/create/edit/delete **account** tokens via `/accounts/{id}/tokens/*` —
  no Global Key needed. This is the preferred path.
- **User-scope token ops still need the Global API Key**
  (`X-Auth-Key` + `X-Auth-Email`) or an OAuth user session — scoped tokens
  cannot touch `/user/tokens`.
- `CF_TOKEN_FILE=<path>` env var makes `cf_tokens.py` act as a different
  bearer token (the file must contain JSON with a `"value"` key) — used to
  call APIs as a freshly-minted ephemeral token.

#### Ephemeral-minter pattern (used for service-token + D1 writes)

`cloudless-access` lacks `Access: Service Tokens` and `D1` scopes, but its
`Account API Tokens Write` lets it mint a purpose-scoped token on demand:

```python
## 1. mint ephemeral token with just the needed perm group
POST /accounts/{acct}/tokens  {policies: [{permission_groups:[D1 Write,...],
                               resources:{account}}]}
## 2. use token value for the operation
## 3. DELETE /accounts/{acct}/tokens/{id}  — always clean up
```

Values live only in memory / `~/.cache/cf-ops/` (0600) — **do not use /tmp**:
this WSL environment wipes /tmp between sessions (observed 2026-09-23 —
two minted token values were lost and the orphaned tokens had to be
deleted server-side).

### Credentials (repo-root `.env`, never printed/committed)

| Var | Purpose |
|---|---|
| `CLOUDFLARE_ACCESS_TOKEN` | **Re-issued 2026-09-29** (id `935ef92e…`) — user token named `cloudless-access` with `Account API Tokens Write` + `Access: Apps and Policies Write` + `Access: Service Tokens Write` (account scope) + `API Tokens Write` (user scope). The workhorse credential; restores the ephemeral-minter pattern. Replaces the dead `87bc6879…`. |
| `CLOUDFLARE_API_TOKEN` | Live but narrow: Zone Read + DNS + Workers read on `cloudless.gr` only. Cannot list/patch tokens, read zone settings, or touch Access/bots/Turnstile. |
| `CLOUDFLARE_GLOBAL_KEY` + `CLOUDFLARE_EMAIL` | Global API Key — **absent from `.env`** (2026-09-29). Only needed for USER-token ops. Get: dash.cloudflare.com → My Profile → API Tokens → Global API Key → View. |

### Regenerating `cloudless-access` — no dashboard needed

Used 2026-09-29, verified working. `cf auth login --device` (OAuth device
grant — user approves at dash.cloudflare.com/oauth2/device/verify) gives a
user OAuth profile with full perms, then mint via API:

```bash
cd /tmp && env -u CLOUDFLARE_API_TOKEN cf auth login --no-browser --device
##   ↑ run from a dir WITHOUT .env — cf auto-loads CLOUDFLARE_API_TOKEN
##     from cwd and it takes precedence over OAuth
env -u CLOUDFLARE_API_TOKEN cf user tokens permission-groups list   # get IDs
env -u CLOUDFLARE_API_TOKEN cf user tokens create --name cloudless-access \
  --policies @policy.json   # see below; write response to ~/.cache/cf-ops/
```

Policy = one `allow` per scope; resources keyed `com.cloudflare.api.account.<id>`
/ `com.cloudflare.api.user.<id>` with `"*"`. Perm group IDs (verified):
Account API Tokens Write `5bc3f8b2…`, Access Apps+Policies Write (account)
`1e13c512…`, Access Service Tokens Write `a1c0fec5…`, API Tokens Write (user)
`686d18d5…`. Paste the minted value as `CLOUDFLARE_ACCESS_TOKEN=` in repo-root
`.env` — never in chat/commits.

### MCP server (preferred)

`cloudflare` in `.devin/mcp_config.json` →
`.devin/skills/cloudflare-ops/cloudflare-token-ops/scripts/cf-mcp-server.py` (stdio, stdlib
only). Tools: `cf_verify`, `cf_list_tokens`, `cf_list_perm_groups`,
`cf_add_token_perm`, `cf_create_token`, `cf_list_service_tokens`,
`cf_create_service_token`, `cf_list_access_apps`.

### CLI equivalent

`scripts/cf_tokens.py` (repo root) — same operations:

```bash
python3 scripts/cf_tokens.py verify
python3 scripts/cf_tokens.py list
python3 scripts/cf_tokens.py perm-groups --scope account service
python3 scripts/cf_tokens.py add-perm <token-id> "Access: Service Tokens"
python3 scripts/cf_tokens.py create <name> --scope account --perm "Workers Scripts"
python3 scripts/cf_tokens.py service-token cloudless-site-bridge --duration forever
CF_TOKEN_FILE=~/.cache/cf-ops/x.json python3 scripts/cf_tokens.py service-token ...
```

### `cf` CLI (installed 2026-09-29)

Cloudflare's Birthday Week 2026 replacement for Wrangler — full API coverage
(~3,000 ops vs Wrangler's ~280), JSON-first output, agent-oriented.
Installed globally via pnpm (`cf 1.0.0-beta.5`).

```bash
export CLOUDFLARE_API_TOKEN=...        # reads env like Wrangler
cf cli search "natural language task"  # discover the right command
cf security-center insights list --zone cloudless.gr
cf migrate <wrangler.toml|jsonc>       # → cloudflare.config.ts (needs local
                                       #   wrangler ≥4.100 in the project)
```

- Prefer `cf` over raw `curl` for any CF API surface — it wraps everything,
  including endpoints with no friendly REST path.
- Token perms still apply server-side — a 403 from `cf` means the calling
  token lacks the scope, not that the CLI failed.
- Wrangler remains supported (18 months maintenance after cf beta ends);
  no repo migration needed yet. Tracked in cloudless.gr issue #2006.
- `cf auth login` exists (OAuth profiles) but env-token auth is sufficient.

### Secret hygiene

- New token values / service-token secrets go to `~/.cache/cf-ops/*.json`
  (0600) — return the path, never the value.
- Never echo `CLOUDFLARE_*` values or `.env` contents.
- Always DELETE ephemeral minter tokens after use.
- Global key = full account control. Keep it in `.env` (gitignored) only.

### Gotchas

- **Empty `service_tokens` list ≠ no tokens.** A token without
  `Access: Service Tokens Read` gets a silent empty list (HTTP 200,
  `count:0`) — this caused a false "wrong account" diagnosis on 2026-09-23.
  Always check the calling token's perm groups before concluding absence.
- **`any_valid_service_token` in an `allow` policy is not enough** for
  non-browser auth on this zone-scoped app — the service token got
  `service_token_status:false` until a dedicated `non_identity`
  (Service Auth) policy was added. See `cloudflare-ops` skill.
- Zone-scoped `GET/POST /zones/{zone}/access/service_tokens` exists but
  needs zone-scope `Access: Service Tokens` perms — account path suffices
  for the `socialauto-app` use case.
- **Token roll (`PUT …/tokens/{id}/value`) commits server-side even when
  the HTTP response errors.** On 2026-09-23 a roll sent without
  `Content-Type: application/json` returned `400 Invalid request headers`
  but still rolled — old value dead, new value lost. Always send the
  header AND treat `result` as a bare string (not an object). Use
  `cf_tokens.py roll <id>` — it handles both and writes the new value to
  `~/.cache/cf-ops/`.

## D1 sync operations

SocialAuto dual-writes to Cloudflare D1 (primary) and PostgreSQL (failover)
via `app/services/db_router.py`; `app/services/db_sync.py` runs the
bidirectional sync. `D1Client` tracks a daily write budget.

### Known failure modes

| Symptom | Cause | Resolution |
|---|---|---|
| `exceeded D1's free tier daily row write limit` | Free tier cap (~100k row writes/day, resets at UTC midnight) | Wait for reset, reduce sync churn, or upgrade the D1 plan. Postgres keeps serving via circuit breaker. |
| `table 'post_targets' still has no PRIMARY KEY — refusing to upsert` | Table created without PK; `INSERT OR REPLACE` then duplicates rows (post_targets once ballooned to 150k+ rows) | `db_sync._repair_d1_pk` auto-recreates the table as `<table>_repaired` with the correct PK and swaps it — but needs write quota to run. Retry `sync` after quota resets. |
| circuit breaker open | 3+ consecutive D1 failures → 60s Postgres-only window | Self-heals; replay queued writes after recovery. |

### Ops tool

```bash
python3 scripts/d1_ops.py health          # d1/kv/vectorize + write budget
python3 scripts/d1_ops.py status          # router state, replay queue depth
python3 scripts/d1_ops.py tables          # D1 tables + row counts
python3 scripts/d1_ops.py pk post_targets # PASS/WARN on PRIMARY KEY presence
python3 scripts/d1_ops.py sync            # full bidirectional sync (uses quota!)
python3 scripts/d1_ops.py replay          # flush queued writes to D1
```

Or raw endpoints:

```bash
curl http://localhost:8083/api/v1/cf-db/health   # includes d1_write_budget
curl -X POST http://localhost:8083/api/v1/cf-db/sync
curl -X POST http://localhost:8083/api/v1/cf-db/replay
curl http://localhost:8083/api/v1/cf-db/tables
```

### Repair procedure for a PK-less table

1. `d1_ops.py health` — confirm the write budget has headroom. If
   exhausted, wait for the UTC-midnight reset; do NOT force syncs.
2. `d1_ops.py pk <table>` — confirm the table lacks a PK.
3. `d1_ops.py sync` — the sync loop detects the missing PK and runs
   `_repair_d1_pk` automatically (recreate → copy → swap). It logs
   `D1 PK repair failed` if quota blocks it mid-way — safe to re-run.
4. `d1_ops.py pk <table>` again → PASS; `d1_ops.py tables` to sanity-check
   row counts vs Postgres.

### Gotchas

- Every `sync` burns write quota proportional to changed rows — don't
  loop it to "watch" progress.
- The quota error is Cloudflare-side, not the app's budget counter —
  `d1_write_budget` is the app's own tracking and may read lower than
  reality if other tools wrote directly.
- Postgres is always the source of truth for repair; D1 rows are a
  copy. Never treat D1 as authoritative after a quota gap — verify with
  `tables` counts.

## R2 managed public domain

Media served to Threads/TikTok (`PULL_FROM_URL`) must be public HTTPS —
the R2 bucket's managed `*.r2.dev` domain provides it. Two in-container
utilities (run inside `social-api`):

```bash
PYTHONPATH=/app python scripts/check_r2_public_url.py   # is a public URL enabled? prints it
PYTHONPATH=/app python scripts/enable_r2_public.py      # enables the managed domain
```

Both read `CLOUDFLARE_ACCOUNT_ID` / `CLOUDFLARE_API_TOKEN` /
`R2_BUCKET_NAME` from settings and talk to the Cloudflare API directly.
If `check` reports no domain: run `enable`, then confirm `check` prints
a `r2.dev` URL. Private/custom-domain setups supersede this — only
relevant when publish errors show unreachable media URLs.
