---
name: metabase-ops
description: Query SocialAuto's Postgres through Metabase via CognitionAI's metabase-mcp-server (MCP server `metabase`), and manage the Metabase instance itself (admin user, API keys, data sources, setup-token flow). Use when asking analytics/SQL questions, building dashboards, or fixing Metabase access.
---

# Metabase Ops

## What this is

CognitionAI's [`metabase-mcp-server`](https://github.com/CognitionAI/metabase-mcp-server)
(installed at `~/.local/lib/metabase-mcp/node_modules/@cognitionai/metabase-mcp-server`,
registered as the `metabase` MCP server in `.devin/mcp_config.json`) gives full
Metabase access: dashboards, cards, native SQL, database introspection.

The `metabase` compose service is a Metabase instance whose application DB lives
in the shared `postgres` container and which is wired to **`social-postgres` /
`social_automation`** as the "SocialAuto" data source (id 1).

## Credentials — never commit

`.devin/metabase-mcp.env` (gitignored, chmod 600):

```
METABASE_URL=http://localhost:3000
METABASE_API_KEY=mb_...   # "devin-mcp" key, Administrators group
```

`scripts/metabase-mcp.sh` sources it and execs the server. Regenerate a key:

```python
# session auth → POST /api/api-key  (see scripts/bootstrap-metabase.py pattern)
```

## Metabase API quirks (v0.63)

- Session header is **`X-Metabase-Session`** — NOT `X-Session-Token` (older docs).
  Using the old header returns `Unauthenticated` even with a valid session.
- First-boot programmatic setup: `GET /api/session/properties` returns a
  `setup-token` while `has-user-setup` is false; `POST /api/setup` with
  `{token, user, prefs}` creates the admin. The `database` block in setup did
  NOT reliably attach — add sources afterwards via `POST /api/database`.
- Admin creds come from `MB_EMAIL`/`MB_PASSWORD` in repo `.env` (Metabase itself
  ignores those env vars — they were used for the one-time `/api/setup`).

## Adding the SocialAuto DB (if it ever needs redoing)

`POST /api/database` with details `{host: "social-postgres", port: 5432,
dbname, user, password}` from `SOCIAL_POSTGRES_*` in `.env`. Both containers
share the `cu130-db` network.

## Gotchas

- `metabase` gets **idle-slept by stack-ops** like other sidecars — if MCP calls
  fail with connection refused, `docker compose up -d metabase`, wait ~30s for
  health, retry.
- The MCP server defaults to `--essential` tool filtering; pass `--all` in the
  wrapper args if a tool is missing.
- `npx`/`npm` shims under `~/.local/bin` are broken on this host (fnm points at
  a nonexistent install). Always invoke `node .../dist/server.js` directly, as
  the wrapper does — never `npx @cognitionai/metabase-mcp-server`.
