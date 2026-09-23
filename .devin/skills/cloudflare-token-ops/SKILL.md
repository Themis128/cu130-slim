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
| `CLOUDFLARE_ACCESS_TOKEN` | Account token `cloudless-access` (id `87bc6879…`) — Access apps/policies read+write + `Account API Tokens Write`. The workhorse credential. |
| `CLOUDFLARE_GLOBAL_KEY` + `CLOUDFLARE_EMAIL` | Global API Key — only needed for USER-token ops. Get: dash.cloudflare.com → My Profile → API Tokens → Global API Key → View (account owning cloudless.gr). |

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
