# Cloudflare Token Ops

Manage Cloudflare API tokens and Access service tokens programmatically for
the cloudless.gr account (`fb7dc7b69b662480cd5961a4d1913c78`).

## When to use

- Adding a permission group to an existing API token
- Creating user-scoped or account-scoped API tokens
- Creating/listing Cloudflare Access service tokens
- Checking which CF credentials are configured and working

## Auth model (verified 2026-09-23)

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

### Ephemeral-minter pattern (used for service-token + D1 writes)

`cloudless-access` lacks `Access: Service Tokens` and `D1` scopes, but its
`Account API Tokens Write` lets it mint a purpose-scoped token on demand:

```python
# 1. mint ephemeral token with just the needed perm group
POST /accounts/{acct}/tokens  {policies: [{permission_groups:[D1 Write,...],
                               resources:{account}}]}
# 2. use token value for the operation
# 3. DELETE /accounts/{acct}/tokens/{id}  — always clean up
```

Values live only in memory / `~/.cache/cf-ops/` (0600) — **do not use /tmp**:
this WSL environment wipes /tmp between sessions (observed 2026-09-23 —
two minted token values were lost and the orphaned tokens had to be
deleted server-side).

## Credentials (repo-root `.env`, never printed/committed)

| Var | Purpose |
|---|---|
| `CLOUDFLARE_ACCESS_TOKEN` | ⚠️ **DEAD since ~2026-09-28** (`9109 Invalid access token`). Account token `cloudless-access` (id `87bc6879…`) — Access apps/policies read+write + `Account API Tokens Write`. Regeneration recipe below. |
| `CLOUDFLARE_API_TOKEN` | Live but narrow: Zone Read + DNS + Workers read on `cloudless.gr` only. Cannot list/patch tokens, read zone settings, or touch Access/bots/Turnstile. |
| `CLOUDFLARE_GLOBAL_KEY` + `CLOUDFLARE_EMAIL` | Global API Key — **absent from `.env`** (2026-09-29). Only needed for USER-token ops. Get: dash.cloudflare.com → My Profile → API Tokens → Global API Key → View. |

## Regenerating `cloudless-access` (dashboard-only, ~2 min)

Account tokens cannot be created by other tokens once the minter is dead —
this MUST be done in the dashboard:

1. dash.cloudflare.com → **Account** (cloudless.gr account
   `fb7dc7b69b662480cd5961a4d1913c78`) → **API Tokens** → **Create Token**
   → Custom token
2. Permission groups:
   - `Account` → `API Tokens` → **Edit** (restores the ephemeral-minter
     pattern)
   - `Account` → `Access: Apps and Policies` → **Edit**
   - `Account` → `Access: Service Tokens` → **Edit** (the old token lacked
     this — caused the silent-empty-list bug)
3. Account resources: this account. TTL: none.
4. Paste the value as `CLOUDFLARE_ACCESS_TOKEN=` in repo-root `.env`
   (gitignored) — never in chat/commits.

## MCP server (preferred)

`cloudflare` in `.devin/mcp_config.json` →
`.devin/skills/cloudflare-token-ops/scripts/cf-mcp-server.py` (stdio, stdlib
only). Tools: `cf_verify`, `cf_list_tokens`, `cf_list_perm_groups`,
`cf_add_token_perm`, `cf_create_token`, `cf_list_service_tokens`,
`cf_create_service_token`, `cf_list_access_apps`.

## CLI equivalent

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

## `cf` CLI (installed 2026-09-29)

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

## Secret hygiene

- New token values / service-token secrets go to `~/.cache/cf-ops/*.json`
  (0600) — return the path, never the value.
- Never echo `CLOUDFLARE_*` values or `.env` contents.
- Always DELETE ephemeral minter tokens after use.
- Global key = full account control. Keep it in `.env` (gitignored) only.

## Gotchas

- **Empty `service_tokens` list ≠ no tokens.** A token without
  `Access: Service Tokens Read` gets a silent empty list (HTTP 200,
  `count:0`) — this caused a false "wrong account" diagnosis on 2026-09-23.
  Always check the calling token's perm groups before concluding absence.
- **`any_valid_service_token` in an `allow` policy is not enough** for
  non-browser auth on this zone-scoped app — the service token got
  `service_token_status:false` until a dedicated `non_identity`
  (Service Auth) policy was added. See `cloudflare-access-paths` skill.
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
