# funnel-sync-ops

Operate the SocialAuto monetization backbone — the hourly `run_funnel_sync` beat (EspoCRM→Mautic contacts + MinIO funnel-zone export). The sync itself is healthy and saturated; the real levers are upstream lead-gen and segmentation.

## Architecture — the monetization loop

```
Lead sources (upstream)          CRM loop                    Monetization
LinkedIn cloudless.gr/contact →  EspoCRM (202 leads)  →  Mautic segment crm-sync (197)
Survey responses             →                          →  email campaigns
X social.cloudless.gr/pricing →  (Polar billing)      →  funnel-zone → MinIO lake
```

- `run_funnel_sync` (hourly, `default` queue) = `mautic_sync.py --apply` + `lake_export.py --apply`
- Verified 2026-10-31: 202 EspoCRM leads, 3 new synced, 197 already in `crm-sync` — **the sync is saturated, not broken**.

## The bottleneck is NOT the sync

The loop's healthy — 97% of leads already flow into Mautic. The funnel grows by:
1. **Lead-gen volume** — more inbound at `cloudless.gr/contact` (LinkedIn CTA) + survey responses + `/pricing` Polar.
2. **Segmentation** — split `crm-sync` into scored segments (hot/cold, by-source) so campaigns target hot leads, not one flat list.
3. **Campaign execution** — synced contacts ≠ sent emails; confirm the Mautic campaign fires, not just enrollment.

## Verify

`celery_app.tasks` does NOT show `include` modules — false negative. Always check the live worker:
```bash
docker exec social-worker-default celery -A app.worker.celery_app inspect registered | grep funnel
```

## Tool — scripts/funnel_sync_tool.py (inside social-worker-default)

`status` | `trigger` | `check` | `logs`

## If it fails

- EspoCRM creds → `espocrm-ops`/`crm-ops`; Mautic down → `mautic`+`mautic-db`; MinIO → `minio`; beat → `celery inspect active` on celery-beat.
