# mcp-ops

Operate the repo's MCP server fleet (`.devin/mcp_config.json`): health-probe every
server, close credential gaps (Metabase API key, Polar token), and explain
sleep-managed URL servers. Use when an MCP server fails to list tools, a
credential is missing, or after editing `mcp_config.json`.

## Layout gotchas (Windows host + WSL bash)

- `bash` on this box resolves to **WSL** bash; `C:\Program Files\Git\bin\bash.exe`
  is Git Bash. `.sh` MCP wrappers auto-detect and use Windows node at
  `C:\Program Files\nodejs\node.exe` + packages under
  `C:\Users\tbaltzakis\.local\lib\`.
- `mcp-remote` (for `cloudflare-api`, `cloudflare-docs`, `slack`,
  `slack-cloudless`, `polar`) is installed once at
  `C:\Users\tbaltzakis\.local\lib\mcp-remote\` — do NOT use `npx` per-launch.
- `.sh` wrappers must stay LF (CRLF = `set: pipefail: invalid option`).
  Rewrite via `python` or the edit tool, never `cat >>`.
- Passing `/mnt/c/...` args to `wsl` **from MSYS** gets mangled
  (`C:/Program Files/Git/mnt/...`); set `MSYS2_ARG_CONV_EXCL='*'` when testing.

## Tools (run from repo root)

```bash
# Probe ALL servers — commands get an initialize handshake, urls get an HTTP GET
python .devin/skills/mcp-ops/scripts/mcp_health.py
python .devin/skills/mcp-ops/scripts/mcp_health.py --only cloudflare,n8n --timeout 10
python .devin/skills/mcp-ops/scripts/mcp_health.py --json

# Metabase — unattended: waits for the container (up -d, retries through Hub
# rate-limits), logs in with MB_EMAIL/MB_PASSWORD from .env, mints a
# "devin-mcp" Administrators API key (idempotent — re-mints if it exists),
# writes .devin/metabase-mcp.env, smoke-tests the wrapper.
python .devin/skills/mcp-ops/scripts/mb_api_key_mint.py [--wait 600]

# Polar — dashboard-gated token: validate + write a supplied token to .env,
# or report state. Cannot mint unattended (Polar dashboard requires
# interactive login — same irreducible-auth class as Cloudflare tokens).
python .devin/skills/mcp-ops/scripts/polar_token_setup.py            # state
python .devin/skills/mcp-ops/scripts/polar_token_setup.py <token>    # validate+write
python .devin/skills/mcp-ops/scripts/polar_token_setup.py --test     # handshake
```

## Sleep-managed URL servers

`linkedin` (127.0.0.1:9227), `airbyte` (9228), `nextcloud` (9231) point at
compose sidecars managed by **stack-ops idle-sleep**. A `000`/`fail` probe means
the container is asleep, not broken:

```bash
python scripts/stackctl.py status          # which are running/asleep
python scripts/stackctl.py wake <name>     # start + wait healthy
```

Do not `depends_on` them or probe their ports for health — a probe is a wake.

## Server inventory quirks

- `telegram` was removed from `mcp_config.json` (never installed here; the
  platform-provided `telegram` server already exists).
- `playwright` runs in docker (`mcr.microsoft.com/playwright/mcp:v0.0.82`,
  image pulled locally); mounts point at `C:/Users/...`, `.playwright-data/`
  holds `pw-mcp-config.json` + `init-script.js`. Do NOT mount an empty
  `ms-browsers` dir — it shadows the image's bundled browsers.
- `socialauto` needs `social-api` running (`docker compose ps`).
- Platform-side servers (gmail, slack-remote, grafana, etc.) come from
  `%APPDATA%\devin\mcp_config.json`, not this file — repo config can't fix them.

## Verification

`python .devin/skills/mcp-ops/scripts/mcp_health.py` exit 0 = every probed
server alive. Expect `metabase`/`polar` to fail with credential errors until
the gaps above are closed — the scripts tell you exactly which var is missing.
