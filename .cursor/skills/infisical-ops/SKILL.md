# Infisical Ops

Self-hosted Infisical secrets manager on the WSL docker stack. Companion to `authentik-ops` (identity) and `cloudflare-ops` (token minting).

## Deployment shape (deployed 2026-11-05)

- Service `infisical` in `docker-compose.yml`, image `infisical/infisical:latest-postgres`, `mem_limit: 768m`, on `cu130-db` network.
- Dedicated DB `infisical` inside the shared `postgres` container — the postgres superuser is `metabase`, NOT `postgres` (the `POSTGRES_*` env vars belong to a different service; using them fails auth).
- `DB_CONNECTION_URI`/`INFISICAL_ENCRYPTION_KEY`/`INFISICAL_AUTH_SECRET` live in `.env` (root-owned file; patch as root). Keys ALSO in `~/.config/secrets/infisical`.
- ENCRYPTION_KEY = 16-byte hex, AUTH_SECRET = base64 32-byte — wrong format crashes with `Invalid key length`.
- Ports: `127.0.0.1:8090:8080` + `100.86.188.43:8090:8080` (tailnet). Health: `GET /api/status` -> `{"message":"Ok","emailConfigured":true,...}`.
- Public: `https://secrets.cloudless.gr` gated by authentik proxy provider (see authentik-gate-ops). OIDC SSO is Pro-tier paywalled — the proxy gate is the free-tier path; an `infisical` OIDC provider (pk23) is pre-staged if Pro is ever bought.
- Admin: `tbaltzakis@cloudless.gr` (password in `~/.config/secrets`). Org: `Admin Org`, project `cloudless-infra` id `27660119-de58-4d8b-adaf-a39c8fc8c79e`, envs dev/staging/prod.

## Machine auth (Universal Auth)

Identity `social-api` id `1a9b4bec-72c9-4bdc-a467-7a3509532fca`, clientId/clientSecret in `~/.config/secrets/infisical` (INFISICAL_UA_CLIENT_ID / _SECRET).
Login: `POST /api/v1/auth/universal-auth/login {clientId,clientSecret}` -> `accessToken` (JWT, TTL 2592000s).

## API facts (web session is NOT bearer-usable)

- The UI session is HttpOnly-cookie + in-memory JWT; `fetch` from the page context gets 401 `Token missing`. Always use universal-auth from scripts.
- Create project: `POST /api/v2/workspace {projectName}` (v3 route does NOT exist -> 404).
- Push secret: `POST /api/v3/secrets/raw/{name} {workspaceId, environment, secretValue, secretPath:"/", type:"shared"}`.
- List: `GET /api/v3/secrets/raw?workspaceId=..&environment=prod`.

## Tools

`scripts/push-secret.sh <NAME> <value-file|->` — logs in via universal-auth (creds from `/root/.config/secrets/infisical` — note: root's home, not `/home/tbaltzakis`), pushes to `cloudless-infra`/prod, masks value in output.

## Bulk sync (added 2026-11-09)

`/home/tbaltzakis/home-ops/scripts/infisical-sync.py` (home-ops repo) is the
canonical bulk tool — `.env` is now a generated cache, Infisical prod is the
source of truth:

- `push <envfile>` — upsert every real KEY=VALUE (skips comments/empties/
  placeholders). Creates missing; `--overwrite` updates existing. Prints key
  names + status only, never values. Verified: all 219 live `.env` keys are in
  prod (252 total incl. 33 Infisical-only).
- `pull --out <file>` — write Infisical secrets as dotenv. Merge mode:
  Infisical wins conflicts, local-only keys preserved, timestamped `.bak-*`
  backup, mode 600. `--force` writes Infisical-only.
- `status <envfile>` — name/count diff between file and Infisical.

Cron `/etc/cron.d/infisical-sync` pulls prod → `.env` daily 05:30
(`~/logs/infisical-sync.log`). Env changes still need `docker compose up -d`.
New secrets: add to Infisical (UI/`push-secret.sh`/`infisical-sync.py push`),
never hand-edit `.env` long-term.

Gotchas learned the hard way:
- `GET /api/v3/secrets/raw` PAGINATES — always pass `limit=1000` or ~26% of
  keys silently go missing (bit us: 66 keys invisible → false "missing").
- Error bodies are HTTP 4xx + `{"statusCode":400,"message":"Secret already exist"}`
  — check statusCode, don't count any JSON as success.
- Helper `/root/.local/bin/infisical-api.sh` curls INSIDE the `infisical`
  container (no published-port dependency) with a cached UA token.
- Multiline secret values are written as literal `\n` in `.env` — fine for
  current consumers; revisit if a consumer needs real newlines.

## Gotchas

- `docker compose config` in a git worktree fails on missing `.env` (gitignored) — copy it in before validating.
- Repo convention: `.env.example` must list every new `${VAR}` or the CI `validate` gate fails.
- Any edits to root-owned files (`docker-compose.yml`, `.env`, `.git/objects`): run as `wsl -u root` or fix ownership first.
