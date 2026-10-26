# linkedin-ads-mcp — LinkedIn Marketing API via MCP

Nuraveda `linkedin-ads-mcp` registered in `.devin/mcp_config.json`
(uvx runtime). Provides read + write campaign ops through MCP tools —
all writes default DRAFT (nothing goes live accidentally); promote
group→ACTIVE then campaign PAUSED→ACTIVE in two explicit steps.

## Tools

Read: `list_ad_accounts`, `list_account_users`, `list_campaign_groups`,
`list_campaigns`, `list_creatives`, `get_account_analytics(days)`,
`get_campaign_analytics(days)`.
Write: `create_campaign_group`, `create_campaign` (TEXT_AD, DRAFT),
`update_campaign_status`, `update_campaign_group_status`.

## Live state (verified 2026-10-26)

- Token scopes: `r_ads r_ads_reporting` — read tools work NOW;
  `rw_ads` write scope needs one re-consent (LINKEDIN_EXTRA_SCOPES set).
- Account: `512642510` "Baltzakis Ad Account" (ACTIVE) — $0 spent yet.
- Backend service `app/services/linkedin_ads.py` mirrors the reads for
  worker tasks (snapshot :45, leadgen→CRM :50) — prefer the MCP for
  ad-hoc ops, the service for scheduled work.

## Companion tooling worth adopting later

- `Bin-Huang/linkedin-ads-cli` — targeting-facet explorer + forecasting
  CLI (audience sizing before campaign creation).
- `ivangfalco/linkedin-ads-skill` — full campaign-format script suite
  (thought-leader ads are the B2B cheat code for our SMB funnel).
- airbyte `linkedin-ads` connector — if ads data ever needs to land in
  Metabase/DuckDB analytics.

## Best-practice funnel for cloudless.gr

TECH_STACK winners → organic gate → thought-leader/single-image DRAFT
campaign → €10/day cap → lead-gen form → `linkedin_leads_sync` (:50)
drops responses into `leads` table → nurture. Never ship spend without
an explicit ACTIVE promotion step.
