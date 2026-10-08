# stack-ops — Idle-Sleep & Wake-on-Connect Layer

> Added 2026-10-07 (PRs #364, #366). Motivation: the full compose stack
> pinned ~4.4 GB inside the WSL2 VM (which never returns memory to
> Windows), and browsers/ComfyUI balloon during work — the host froze
> under pressure. Now idle-tolerant services stop when unused and wake
> transparently on first request.

## Architecture

```
caller (backend, n8n, browser, curl)
        │
        ▼  http://stack-ops:<service-port>  (or localhost:<port> from host)
┌──────────────────────────────────────────────────────────┐
│ stack-ops container (~60 MB, python asyncio, docker-cli) │
│                                                          │
│  TCP proxy listener per service ──┐                      │
│    on connect:                    │                      │
│      container running? ──────────┼── yes → pipe bytes   │
│      no → docker start (+deps) → wait healthy → pipe     │
│      browser GET + wait_page → 503 splash + auto-refresh │
│                                                          │
│  Idle sleeper (every POLL_SECONDS):                      │
│    idle ≥ idle_min  AND  cpu < 3% × 2 polls              │
│    (× 4 polls while proxy conns open)  →  docker stop    │
│                                                          │
│  Control API :8787 (host 127.0.0.1 only)                 │
└──────────────────────────────────────────────────────────┘
        │ docker.sock (rw — required for start/stop)
        ▼
   stopped containers wake on demand; sessions persist in volumes
```

Reference designs: kamal-proxy scale-to-zero, GoDoxy idle-sleep,
wake-on-request — same pattern (hold request → wake → pipe).

## Service classes

- **Never sleep** (core — anything *not* in `stack-ops/services.json`):
  social-api, social-frontend, api-gateway, workers ×4, celery-beat,
  redis, postgres ×2, minio, chroma, n8n, cloudflared, social-metrics,
  dmr-watchdog, stack-ops itself.
- **Sleep-managed** (21 keys, 18 canonical containers): browser-novnc
  (+ui/ui2/vnc aliases on 6080/6081/5900), the 4 platform sidecars,
  messenger-sidecar, instagram-private-api, warp-proxy, languagetool,
  n8n-sandbox, metabase, social-jupyter, flower, portainer,
  env-manager-frontend+backend, linkedin/airbyte MCP servers, comfyui.

## services.json schema

```jsonc
{
  "services": {
    "<key>": {
      "target": "container:port",        // required
      "idle_min": 20,                    // idle TTL before sleep
      "container": "real-docker-name",   // only when key != container
      "group": ["warp-proxy"],           // deps woken first
      "wait_page": true                  // UI ports only
    }
  },
  "listeners": { "<stack-ops port>": "<service key>" }
}
```

Mounted read-only into the container — edit + `docker compose up -d
--force-recreate stack-ops` to apply (no image rebuild).

Alias rule: a key is a *secondary alias* (skipped by the sleeper) only
when its `container` is itself another service key. `comfyui` →
`social-media-comfyui-gpu` is NOT an alias — it owns that container and
the sleeper manages it.

## Control API (`:8787`)

| Route | Effect |
|---|---|
| `GET /healthz` | liveness |
| `GET /status` · `/status/<name>` | all/one service → `{container, state, last_active_ago_s, active_conns, keepawake_s}` |
| `POST /wake/<name>` | start + wait healthy (deduped per container) |
| `POST /sleep/<name>` | `docker stop -t 30` — returns real result |
| `POST /sleep-all` | sleep every canonical service |
| `POST /keepawake/<name>?ttl=<s>` | suspend auto-sleep (default 2h) |

`scripts/stackctl.py` wraps it: `status`, `wake`, `sleep`, `keepawake`,
`sleep-all`, `savings`.

## Request-path semantics

- **TCP-transparent** — works for HTTP, WebSocket, SOCKS5, VNC, MCP
  streamable-http. Caller base-URL host is simply `stack-ops`.
- **Cold-start latency** ~5–13 s (comfyui/metabase up to ~30 s). Client
  timeouts ≥ 60 s are safe (all backend sidecar clients use 120–300 s).
- **wait_page**: only for `GET` + `Accept: text/html` (browser
  navigation) — returns an auto-reloading splash while the wake runs in
  background. API verbs and non-HTTP protocols block through the wake
  and get the real response; on wake failure HTTP clients get JSON 503.
- **Wake groups**: `warp-proxy` is woken before
  `linkedin-browser-sidecar` and `instagram-private-api` (their traffic
  needs the residential IP).

## Compose rules (do not regress)

- **No `depends_on` on managed services** — compose starts named deps on
  every targeted `up`, defeating sleep. Callers reach them via the proxy.
- Managed services keep `restart: unless-stopped` (`docker stop` keeps
  them down; comfyui is `restart: no` — on-demand by policy).
- Host ports for managed services are published **by stack-ops**, not the
  backend container (comfyui keeps `localhost:8000` via internal
  listener 8199; instagram-private-api is `localhost:8011`).
- A full `docker compose up -d` still starts every defined service —
  the sleeper re-sleeps them within `idle_min`. Prefer targeted ups.
- Tunables via `.env`: `STACK_OPS_POLL_SECONDS` (60), `STACK_OPS_CPU_MAX`
  (3.0), `STACK_OPS_WAKE_TIMEOUT` (120), `STACK_OPS_CONN_STREAK` (4),
  `STACK_OPS_SLEEPER` (1).

## Health probes must not wake

- `GET /status` uses `docker inspect` only — safe to poll.
- Backend `ops_console` and `platform-mcp-server` consult stack-ops
  (`app/services/stack_ops.py`) and report `detail="sleeping"` instead of
  probing sleeping services. Status is keyed by *service* name; entries
  carry `container` so docker-name lookups also resolve.

## Host watchdog integration (Windows)

`\DevOptimizer\Docker-WSL-Sync` (30 min schedule) mass-starts Exited
`unless-stopped` containers — it now fetches `localhost:8787/status` and
skips stack-ops-managed names, falling back to resurrect-all when
stack-ops is down (safe: nothing is asleep then). Its port-proxy healing
restarting `stack-ops` on a dead proxy port is a *correct* remedy (rebind).

WSL memory: `.wslconfig` uses `autoMemoryReclaim=dropCache` — the known-
bad combo is Docker Desktop Resource Saver + `gradual`. Keep `dropCache`;
see `windows-host-ops` skill.

## Failure modes & troubleshooting

| Symptom | Check |
|---|---|
| `Exited` container | Probably asleep — `stackctl status`, don't restart manually |
| Service won't sleep | `stackctl status` → conns/keepawake; `docker logs stack-ops` for streak resets; CPU from out-of-band work keeps it up by design |
| Wake times out (120s) | `docker start <container>` manually, check healthcheck/logs; sleeper never stops `starting` services |
| `compose up` woke everything | expected — full-stack up starts all; sleeper re-sleeps |
| Sidecar session died | sessions persist in volumes (`browser_profile`, `linkedin_browser_data`, …) — sleep never drops them |
| Weird commits while editing | repo auto-commit hook (see AGENTS.md "Commit & push cadence") — verify `git log` before pushing |

Tests: `cd stack-ops && python3 -m unittest test_stack_ops -v`
(29 tests — config integrity, alias rules, wait-page predicates, control
API, pipe path; docker calls are patched, no containers needed).
