# LinkedIn Intel Ops — Ad Library watch pipeline

Competitive-intelligence pipeline over LinkedIn's **Ad Library API**
(`GET https://api.linkedin.com/rest/adLibrary`, Default Tier — granted on
developer apps `265077368` + `264925843`).

## What runs where

| Piece | Location |
|---|---|
| Tables | `linkedin_ad_intel` (observations), `linkedin_intel_watch` (watchlist) — migration `a2b3c4d5e6f7` |
| Service | `app/services/linkedin_ad_library.py` — `run_watch()`, `search_all()`, `search_ads()` |
| Celery task | `app.worker.tasks.linkedin_intel.run_linkedin_ad_library_watch` |
| Beat | `linkedin-ad-library-watch` — **Mon 09:00** `APP_TIMEZONE` (Europe/Athens) |
| Queue/route | `default`; annotations 1800s soft / 2100s hard |
| Alert | `send_telegram_alert("intel", …)` → #intel (`-1004362141941`) |
| Workforce audit | `linkedin-adlib-watch` entry in `scripts/workforce_audit.py` |

## API surface (`/api/v1/linkedin/intel/*`, auth required)

- `GET /intel/ads?status=active|ended|all&advertiser=&keyword=&limit=&offset=`
- `GET /intel/summary` — totals, top advertisers, 10 newest ads
- `GET /intel/watchlist` — list watches
- `POST /intel/watchlist` `{kind: keyword|advertiser, value, country=GR, enabled}` — team-admin
- `DELETE /intel/watchlist/{id}` — team-admin
- `POST /intel/run` — enqueue an ad-hoc watch run — team-admin

## Behavior contract

- First run **auto-seeds** `DEFAULT_WATCHES` (GR SMB keywords + AWS/GCP/Azure
  advertiser watches) when the watchlist is empty.
- Upsert key = `ad_url` (unique). Re-observed ads reset `miss_count=0`,
  `status=active`, refresh `last_seen_at`.
- **Ended detection**: an active ad absent from results accrues `miss_count`;
  at `MISS_THRESHOLD=2` consecutive misses → `status='ended'`.
- Per-watch errors are collected into the run summary (never kill the sweep);
  summary posts to #intel every Monday.
- Page pacing: 5s between pages, ≤10 pages/watch, `count=50`.

## API quirks (verified live 2026-11-06)

- `Linkedin-Version` must be an **active monthly** version — currently
  `202610`; stale months return `426 NONEXISTENT_VERSION`. Bump
  `HDRS["Linkedin-Version"]` when LinkedIn rotates (watch for 426s in the
  #intel digest `err:` lines).
- `X-Restli-Protocol-Version: 2.0.0` required.
- Token: org LinkedIn `SocialAccount` → `decrypt_token` in-process only.

## Ad-hoc CLI

`backend/scripts/linkedin_adlibrary.py` — `accounts` / `search --keyword|--advertiser`
(one-shot, no DB writes). See `linkedin-graph-ops` / PR #1396.

## Metabase / decision surfaces

Useful queries over `linkedin_ad_intel`:

```sql
-- Who is active in our niche right now
SELECT advertiser, count(*) AS ads, max(impressions_to) AS max_impr
FROM linkedin_ad_intel WHERE status='active'
GROUP BY advertiser ORDER BY ads DESC;

-- Long-running ads = proven messaging (swipe-file candidates)
SELECT advertiser, ad_url, impressions_from, impressions_to,
       latest_impression_at - first_impression_at AS flight_ms
FROM linkedin_ad_intel
WHERE status='active' AND latest_impression_at IS NOT NULL
ORDER BY flight_ms DESC NULLS LAST LIMIT 20;

-- Ended this week = market gaps / campaign fatigue signals
SELECT advertiser, ad_url, last_seen_at
FROM linkedin_ad_intel
WHERE status='ended' AND last_seen_at > now() - interval '7 days';
```

Pending follow-ups (later phases): strategy-brief section consuming
`/intel/summary`, `adAnalytics`→`ad_daily_metrics` verification, conversions
/ matched-audiences / lead-webhook services behind feature flags.
