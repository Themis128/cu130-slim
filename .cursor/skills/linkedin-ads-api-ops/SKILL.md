# linkedin-ads-api-ops

LinkedIn Marketing API operations for app **264925843** ("Cloudless
Analytics App", client `77j8kzfi1a6jn8") — campaigns, ad accounts,
analytics, member analytics. Advertising API **Development Tier
approved 2026-11-18**; `rw_ads` + `r_ads` + `r_ads_reporting` granted on
the member token (re-consented 2026-11-17, full 22-scope union).

## Tool

`backend/scripts/linkedin_ads_tool.py` — run inside a worker container:

```bash
docker exec -w /app -e PYTHONPATH=/app social-worker-media \
  python3 scripts/linkedin_ads_tool.py <cmd>
```

| Cmd | What |
|---|---|
| `accounts` | ad accounts visible to the token |
| `campaigns [--status A,B]` | adCampaigns on `LINKEDIN_AD_ACCOUNT_ID` (default `512642510`) |
| `groups` | adCampaignGroups |
| `campaign <id>` | single entity (name/status/budget/schedule) |
| `analytics [--days N]` | spend/clicks/impressions pivot=CAMPAIGN |
| `pause\|resume <id> [--execute]` | status write — **dry-run unless `--execute`** |
| `allowlist` | is the ad account in the app's Dev-Tier portal allowlist? (no-op write probe) |
| `member-analytics` | verifies `r_member_profileAnalytics`/`r_member_postAnalytics` grants live |

## Verified live facts (2026-11-18)

- `GET /rest/adAccounts/{acct}/adCampaigns/{id}` → 200 (entity read works)
- Write = `POST /rest/adAccounts/{acct}/adCampaigns/{id}` +
  `X-RestLi-Method: PARTIAL_UPDATE` + `{"patch":{"$set":{"status":"PAUSED"|"ACTIVE"}}}`
  — **account-scoped path only**; `/rest/adCampaigns/{id}` 404s
- `Linkedin-Version: 202610` + `X-Restli-Protocol-Version: 2.0.0` on every call
- Member analytics endpoints (need version ≥202506):
  - `memberFollowersCount?q=me` → `r_member_profileAnalytics` — **856 followers**
  - `memberCreatorPostAnalytics?q=me&queryType=…&aggregation=…` → `r_member_postAnalytics` — **65,911 impressions**
- Only campaign on account `512642510`: `904916326` "Boost Post Engagement
  September 23" — status `COMPLETED` (pause/resume refused by design)

## THE gate: Dev-Tier ad-account allowlist

Development Tier manages **max 5 ad accounts** that must be listed in the
developer portal — there is NO API/CLI path to add them:

`developers.linkedin.com/developers/apps/264925843` → **Products tab →
"View settings" next to Advertising API → Ad Accounts** → add
`sponsoredAccount:512642510`.

~~Until allowlisted~~ **DONE 2026-11-18**: `512642510` added via CDP
automation (`linkedin-portal-ops/scripts/li_ads_add.py`) — portal shows
"Ad Account was successfully added", `allowlist` probe now reports
ALLOWLISTED (no-op write accepted).

If a NEW ad account is added later, re-run `li_ads_add.py <id> --execute`
(max 5 on Dev Tier). Standard Tier (production spend) is a separate
application afterwards.

## Consumers

- `app/services/linkedin_ads_control.py` — Slack pause/resume/status;
  Marketing API first (PR #2349), sidecar scrape fallback
- `app/services/linkedin_ads.py` — adAccounts/campaigns reads (ads_guard,
  snapshot)
- `app/services/linkedin_ads_report.py` — daily spend report
  (`linkedin-ads-report` beat) + `collect_metrics` for the Slack status action
- `app/worker/tasks/linkedin_ads_guard.py` — daily ACTIVE-campaign
  allowlist guard (read-only)
- `app/services/analytics_sync.py` — member analytics consumer
  (`r_member_postAnalytics`/`r_member_profileAnalytics` now live)

## Gotchas

- `LINKEDIN_ADS_CAMPAIGN_ID` env must be set for the Slack control lane —
  unset = feature inert (returns "not configured")
- Status enum on write: `ACTIVE|PAUSED|ARCHIVED|DRAFT`; `COMPLETED`/
  `ARCHIVED` campaigns refuse pause/resume
- Campaign ≠ ad set: `adCampaigns` are the sets; `adCampaignGroups` are
  the campaigns in Campaign Manager UI terms
- Writes need `rw_ads` — granted; Dev-Tier allowlist is the remaining gate
