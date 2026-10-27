---
name: skool-ops
description: >
  Operate the Sofia Kakkava Skool community integration — check the shared
  browser bridge (detects 409 'busy' contention and retries with backoff),
  verify the Skool session is logged in, run trend scans, and push community
  digests into Nextcloud IntraVox as en/news pages. Use when a skool_sync /
  trend_scan returns empty, when 'browser busy' 409s appear, or when wiring
  the Skool→IntraVox digest. Tool: scripts/skool_ops.py. Requires
  browser-novnc awake; one manual Skool login seeds the session.
---

# Skool Ops — community sync + IntraVox digest

Community: `https://www.skool.com/sofia-kakkava-coaching` via the shared
`browser-novnc` bridge (`BROWSER_BRIDGE_URL`, same session session_healer uses).
IntraVox hub: page `kakkava-visibility-era` under nav `nav_social`.

## Known failure modes (burned 2026-10-27)

- **`trend_scan` → `{'top': []}` is ambiguous** — means login wall, empty
  page, OR the bridge returned 409 "busy with <other> session" and the service
  silently swallowed it. Always run `skool_ops.py status` first: it surfaces
  the real bridge state (free/busy/who-holds-it) that `trend_scan` hides.
- **Shared-session contention** — facebook/linkedin session_healer holds the
  bridge for long runs. `skool_ops.py scan --wait` retries with backoff; the
  service-level fix (retry or dedicated sidecar) is in the backlog.
- **Session seeding** — first run needs ONE manual Skool login inside
  browser-novnc (session persists via the bridge). `status` reports
  `logged_in: false` until then.

## Commands — `scripts/skool_ops.py`

```bash
python3 skool_ops.py status              # bridge free? who holds it? skool logged in?
python3 skool_ops.py scan [--wait]       # trend_scan with retry on 409
python3 skool_ops.py login --email= --password=   # store creds in secret store + heal now
python3 skool_ops.py push-intravox       # scan → digest page in en/news/kakkava-digest-<date>
```

Runs **inside** `social-api` (`docker compose exec -T social-api python
/fix/skool_ops.py …`) for bridge + API access; `push-intravox` execs the
host `intravox_tool.py` — or set `INTRAVOX_TOOL=/path` / run the scan part
in-container and the page-create part on host.

## Pipeline intent

`celery-beat` hourly `skool_sync.sync_skool` diffs the feed; when the bridge
is free + session live, `push-intravox` is the manual/cron path that lands a
digest page. Digest convention: `en/news/kakkava-digest-YYYY-MM-DD` with a
heading + text widget per top post (mirrors the socialauto-digest pattern).
