# Metabase — Cloudless BI on the social stack

Metabase (`metabase/metabase:v0.63.x`, port 3000, app DB in the shared
`postgres` container) is the BI surface over the SocialAuto Postgres.

## Current state (2026-10)

- **Admin user** — `MB_EMAIL`/`MB_PASSWORD` from repo `.env`. Metabase ignores
  those env vars itself; they were used once with `POST /api/setup` (the
  `setup-token` from `GET /api/session/properties` while `has-user-setup` is
  false).
- **Data source** — "SocialAuto" → `social-postgres:5432/social_automation`
  (`SOCIAL_POSTGRES_*` creds). Attached via `POST /api/database` — the
  `database` block inside `/api/setup` did not attach it.
- **MCP access** — server `metabase` in `.devin/mcp_config.json` runs
  CognitionAI's `@cognitionai/metabase-mcp-server` through
  `.devin/skills/metabase-ops/scripts/metabase-mcp.sh`, which sources
  `.devin/metabase-mcp.env` (gitignored; `METABASE_URL` + `METABASE_API_KEY`,
  `devin-mcp` key in the Administrators group). Gives agents dashboards/cards/
  native SQL over the SocialAuto DB.

## API quirks (v0.63)

- Session auth header is **`X-Metabase-Session`** — `X-Session-Token` (older
  docs) returns `Unauthenticated`.
- API keys: `POST /api/api-key` `{name, group_id}` while sessioned as admin —
  group id 2 = Administrators on this instance.
- The MCP npm package's stdio entry is `dist/server.js`, not `dist/index.js`;
  run via `node`, not the broken fnm `npx` shim on this host.

## Idle-sleep

`metabase` is fronted by `stack-ops` like the other sidecars — `Exited` means
asleep, not broken. If MCP calls hit connection refused:
`docker compose up -d metabase`, wait for healthy, retry.

## Housekeeping

- Rotate the admin password if it ever appears in plaintext output; rotate the
  `devin-mcp` API key by creating a new one and updating
  `.devin/metabase-mcp.env` (delete the old key in Admin → Settings → API keys).
- See `.devin/skills/metabase-ops/SKILL.md` for the operational runbook.
