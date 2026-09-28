---
name: follower-analytics-guard
description: Diagnose and repair corrupted follower_snapshots series (fake growth/drop percentages in digests like "-826 (-98.2%)" or "+400%"). Use when follower counts jump, drop, or interleave two different series, or when the daily report shows absurd follower deltas. Covers the three write paths in analytics_sync.py, the _plausible_follower_count guard, the LinkedIn endpoint truth table (networkSizes vs followerStatistics), and cleanup SQL.
---

# Follower analytics data-quality guard

`follower_snapshots` holds derived time-series rows — safe to delete corrupted
rows; they repopulate on the next sync. Digest "growth" numbers read this
series directly, so one bad row produces fake deltas like **-98.2%** or
**+400%**.

## Write paths (analytics_sync.py)

Three writers can add a row per account per sync (~30min):

1. **LinkedIn member** — browser sidecar `get_profile_activity()` scrape
   (member `else` branch). Guarded by `_plausible_follower_count`. When the
   sidecar is logged out / rate-limited, it returns 0 or garbage — `followers
   == 0` is never persisted; the `followers` value may be a misread element
   (once read ~952k "network size").
2. **LinkedIn org** — `networkSizes` → `firstDegreeSize` (authoritative total
   for Company Pages). Plausibility-guarded since PR #144.
3. **Generic** — `_persist_follower_snapshot()` runs for every account after
   sync via `_follower_count()` (api/analytics.py). **Skips LinkedIn entirely**
   (sync_linkedin_account is authoritative for member+org). Guarded for all
   other platforms.

## Plausibility guard — `_plausible_follower_count(prev, new)`

Accepts when: no prior positive reading, OR `new <= prev*5` AND (for `prev >=
100`) `new >= prev*0.5`. Rejects logged-out zeros (>5x spike / >50% drop).
`0` and `-1` are never written.

## LinkedIn endpoint truth table (verified live 2026-09-28)

| Endpoint | Returns | Notes |
|---|---|---|
| `rest/v2 networkSizes/{urn}?edgeType=COMPANY_FOLLOWED_BY_MEMBER` | `firstDegreeSize` — **true total** | enum is UPPERCASE on API ≥202305; lowercase `CompanyFollowedByMember` + `start/count` params → HTTP 400 |
| `v2 organizationalEntityFollowerStatistics` | `followerCountsByFunction/Seniority/Industry` — **segmented buckets only** | NO unsegmented total exists. Reading `organicFollowerCount` from a bucket = per-job-function demographic (was the "5 vs 26" bug). Never use as a follower count. |
| `ugcPosts`/`member postAnalytics` | 401/403 | member stats need restricted scopes — sidecar scrape is the only path |

`LinkedInAPIClient.get_follower_count` tries REST then v2, parses
`firstDegreeSize`/`first.totalSize`/`totalSize`, returns `0` on failure —
never raise.

## Verify a series

```sql
SELECT fs.followers, MIN(fs.captured_at) first_seen
FROM follower_snapshots fs JOIN social_accounts sa ON sa.id = fs.social_account_id
WHERE fs.platform = 'linkedin' AND sa.username = '<handle>'
GROUP BY fs.followers ORDER BY first_seen;
```

Interleaved ascending + low series (e.g. `17,18,19,20,2,21,4,22`) = two
writers disagreeing → find which endpoint produced the low values.

## Cleanup (only after the write-path bug is fixed)

```sql
DELETE FROM follower_snapshots fs USING social_accounts sa
WHERE fs.social_account_id = sa.id AND sa.username = '<handle>'
  AND fs.followers <= <bogus_ceiling> AND fs.captured_at >= '<date>';
```

Keep the value filter tight to observed bogus values — do not bulk-delete by
date alone (early legit low counts matter for growth math).

## Symptoms → cause map

- `-98%` drop after real data → a low bogus reading passed the (previously
  spike-only) guard
- two values ~2s apart per cycle → two writers (org networkSizes + generic
  path or sidecar)
- member series stops entirely → sidecar logged out / `rate_limit_until` —
  check `GET http://localhost:9225/health`; persisted `li_session.json` may
  self-heal after cooldown, else manual noVNC re-login
