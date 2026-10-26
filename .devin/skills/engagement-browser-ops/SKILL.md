# engagement-browser-ops — dedicated ingestion sidecar

The engagement engine scrapes through a **dedicated** Playwright/noVNC
sidecar (`engagement-browser`, bridge `:9223` on `cu130-backend`,
noVNC on host `:6082`) — NOT the shared `browser-novnc` pod, whose
platform cookie-refresh sweeps were stealing sessions mid-scrape and
crashing FB/TT ingestion.

## Topology

```
ingestors/actors → ENGAGEMENT_BROWSER_URL (default
  http://engagement-browser:9223, fallback browser-novnc:9223)
```

All files using it: `worker/tasks/engagement_ingest_*.py`,
`services/engagement_actors/*.py` — `BRIDGE` constant per file.

## Container ops

```bash
# start (image is local cu130-slim-browser-novnc — compose build pulls
# and fails on credential helper; run directly instead)
docker run -d --name engagement-browser --network cu130-backend \
  --restart unless-stopped --memory 1536m --memory-swap 4096m --cpus 2 \
  -p 6082:6080 \
  -e SCREEN_WIDTH=1280 -e SCREEN_HEIGHT=800 -e SCREEN_DEPTH=24 \
  -e VNC_PORT=5900 -e NOVNC_PORT=6080 -e BRIDGE_PORT=9223 \
  -e AUDIO_WS_PORT=6081 -e TZ=Europe/Athens \
  -v engagement_cookies:/app/cookies \
  -v engagement_profile:/app/browser-profile \
  -v $PWD/social-automation/uploads:/app/uploads:ro \
  cu130-slim-browser-novnc:latest
docker exec engagement-browser curl -sf http://localhost:9223/health
```

## Session seeding — cookie injection (no manual login)

Chromium profile copies DON'T carry sessions (cookies encrypted to the
source profile). The working path is Playwright `context.add_cookies`
via the bridge:

```bash
# shared browser holds extracted {platform}_storage_state.json files;
# inject into the dedicated pod per platform:
for p in facebook instagram tiktok; do
  docker exec engagement-browser curl -s -X POST \
    http://localhost:9223/session/start -H 'Content-Type: application/json' \
    -d "{\"platform\":\"$p\",\"force\":true}"
  docker exec browser-novnc sh -c \
    "python3 -c 'import json;print(json.dumps({\"cookies\":json.load(open(\"/app/cookies/${p}_storage_state.json\"))[\"cookies\"]}))'" \
    | docker exec -i engagement-browser curl -s -X POST \
      http://localhost:9223/session/cookies \
      -H 'Content-Type: application/json' -d @-
done
```

Refresh: `POST /session/extract` on the source pod first, or re-run
`extract-cookies.py` when storage_state files go stale (cookies rot).

Platform credentials live NOWHERE (not .env, not secret_store) — if a
session dies and can't be re-injected, log in once via
`http://localhost:6082/vnc.html` (Option A) or set
`{PLATFORM}_EMAIL`/`{PLATFORM}_PASSWORD` for `/session/login`.

## Beats

| Task | Cron | Source |
|---|---|---|
| `ingest_fb_groups` | `:00/:30` | `FACEBOOK_GROUP_URLS` (seeded Greek SMB hubs) |
| `ingest_tiktok_tags` | `:15/:45` | `TIKTOK_TARGET_TAGS` |
| `ingest_ig_feed` | `:20/:50` | atomic single-eval extraction (steal-proof) |
| `ingest_linkedin_feed` | `:05/:35` | linkedin-browser-sidecar (separate, cooldown-aware) |

Required env (compose env blocks — explicit vars only, add to BOTH
blocks): `FACEBOOK_GROUP_URLS`, `TIKTOK_TARGET_TAGS`,
`SLACK_ENGAGEMENT_CHANNEL_ID`, `SLACK_ENGAGEMENT_WEBHOOK_URL`.

## Failure modes

- `browser busy` → returns `{"skip": true}` → action stays
  `pending_actor`, retried next sweep. Never a hard failure.
- Page/context closed mid-eval → retryable; check
  `docker logs engagement-browser`.
- `post-not-visible` (IG) → post scrolled out; retryable by design.
