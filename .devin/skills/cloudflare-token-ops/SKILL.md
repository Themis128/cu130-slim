# Cloudflare Token Ops

Manage Cloudflare API tokens and Access service tokens programmatically for
the cloudless.gr account (`fb7dc7b69b662480cd5961a4d1913c78`).

## When to use

- Adding a permission group to an existing API token
- Creating user-scoped or account-scoped API tokens
- Creating/listing Cloudflare Access service tokens
- Checking which CF credentials are configured and working

## Hard rule — Cloudflare auth model

**API tokens cannot create or edit other API tokens.** Token management
(`POST/PUT /user/tokens`, `/accounts/{id}/tokens`) requires the account
**Global API Key** (`X-Auth-Key` + `X-Auth-Email`) or an OAuth user session.
Everything else (Access apps, service tokens, D1, zones) works with scoped
tokens.

## Credentials (repo-root `.env`, never printed/committed)

| Var | Purpose |
|---|---|
| `CLOUDFLARE_GLOBAL_KEY` + `CLOUDFLARE_EMAIL` | Global API Key — unlocks ALL token management. Get: dash.cloudflare.com → My Profile → API Tokens → Global API Key → View (must be the account owning cloudless.gr). |
| `CLOUDFLARE_ACCESS_TOKEN` | Account token `cloudless-access` (id `87bc6879…`) — Access apps/policies read+write, service-token LIST. Cannot create service tokens (lacks `Access: Service Tokens`) until the group is added via `cf_add_token_perm` with the global key. |

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
```

## Secret hygiene

- New token values / service-token secrets are written to
  `/tmp/cf-token-*.json` (0600) — return the path, never the value.
- Never echo `CLOUDFLARE_*` values or `.env` contents.
- Global key = full account control. Keep it in `.env` (gitignored) only.

## Known-good recipes

**Fix missing scope on cloudless-access** (the 2026-09-23 blocker):

```bash
python3 scripts/cf_tokens.py add-perm 87bc6879b2b67255a53abcd305b60302 \
    "Access: Service Tokens"
python3 scripts/cf_tokens.py service-token cloudless-site-bridge --duration forever
# creds land in /tmp/cf-token-svc-cloudless-site-bridge.json — feed to D1, don't print
```

**Service token lives in the wrong account** — `cf_list_service_tokens`
returning empty while the dashboard shows the token means it was created in
another CF account. Verify account ownership before creating anything.
