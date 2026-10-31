# funnel-sync-ops

Operate the SocialAuto funnel-sync service — the hourly Celery beat that syncs EspoCRM→Mautic contacts and exports the funnel-zone into MinIO. Use for "is the funnel sync running", "trigger a sync now", "why are Mautic contacts stale", "check the funnel data flow".

## What it is

`app.worker.tasks.funnel_sync.run_funnel_sync` — runs two scripts on a beat:
- `scripts/mautic_sync.py --apply` — EspoCRM→Mautic contact sync (the CRM→email-automation loop)
- `scripts/lake_export.py --apply` — funnel-zone export into MinIO (the datalake feed)

Registered on the `default` queue; beat schedule `funnel-sync` in `celery_app.py`. This is the monetization/CRM-loop backbone — contacts flow into Mautic for the email funnels.

## Verify (the registry trap)

`celery_app.tasks` does NOT show `include` modules until the worker boots — checking it directly returns a false negative. Always verify via the LIVE worker:
```bash
docker exec social-worker-default celery -A app.worker.celery_app inspect registered | grep funnel
```

## Tool — scripts/funnel_sync_tool.py (run inside social-worker-default)

- `status` — registered? + beat schedule + last run result
- `trigger` — fire `run_funnel_sync` now (manual sync)
- `check` — EspoCRM + Mautic + MinIO reachable?
- `logs` — recent funnel_sync output

## If the sync fails

- EspoCRM creds → `espocrm-ops` / `crm-ops`
- Mautic down → restart `mautic` + `mautic-db` containers
- MinIO unreachable → check `minio` container + bucket
- Beat not firing → `celery inspect active` on celery-beat
