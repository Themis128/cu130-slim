# SocialAuto ↔ cloudless.gr — Integration Architecture

How the SocialAuto platform (`social.cloudless.gr`, this repo) and the
cloudless.gr website exchange data. Verified against live state on
2026-09-21.

## Overview

```
                        ┌──────────────────────────────────────────┐
                        │            cloudless.gr (Next.js)         │
                        │  Pages · Store · Contact · Subscribe      │
                        │  Admin datalake dashboard · EspoCRM       │
                        └───────┬──────────────────────▲────────────┘
                                │                      │
              (1) web analytics │ events         (2)   │ leads
              (HMAC-signed,     │                      │ webhook
               currently        │                      │ (secret hdr,
               broken — see     │                      │  wired, unused)
               §Status)         │                      │
                                ▼                      │
   ┌─────────────────────────────────────────────────────────────┐
   │              SocialAuto (social.cloudless.gr)                │
   │                                                              │
   │  /api/v1/analytics/web/webhooks/cloudless-analytics          │
   │    → web_analytics_events → fan-out GA4/Plausible/Meta CAPI  │
   │                                                              │
   │  leads.py → _post_cloudless_leads_webhook()                  │
   │    → cloudless.gr/api/webhooks/socialauto-leads              │
   │                                                              │
   │  Celery export_datalake (every 6h :10)                       │
   │    → R2 datalake-bucket : lake/socialauto-*.json             │
   └──────────────────────┬───────────────────────────────────────┘
                          │ (3) JSON snapshots in shared R2 bucket
                          ▼
        ┌──────────────────────────────────────────┐
        │  cloudless.gr ETL + admin dashboard       │
        │  materialize-datalake-snapshots.mjs →     │
        │  lake/snapshots/admin-datalake.json →     │
        │  /admin/analytics/datalake                │
        └──────────────────────────────────────────┘

   (4) Email reports: SocialAuto digest/strategy brief
       → omv-ha postfix → Resend → CF Email Routing
       → dovecot mailbox tbaltzakis@cloudless.gr
```

## (1) Web analytics — cloudless.gr → SocialAuto

Browser events on cloudless.gr pages flow into SocialAuto's web-analytics
store and fan out to third-party analytics sinks.

**Path:**

```
browser (track-client-event.ts, TrackedLink)
  → POST /api/analytics/event            (same-origin, unauthenticated relay)
  → sendSocialAutoEventServer()          (lib/socialauto-analytics-server.ts)
      HMAC-SHA256(body, SOCIALAUTO_WEB_ANALYTICS_SECRET)
      → POST social.cloudless.gr/api/v1/analytics/web/webhooks/cloudless-analytics
  → receive_cloudless_event()            (app/api/web_analytics.py)
      verifies x-webhook-signature against WebAnalyticsConfig.webhook_secret
      → ingest_event() → web_analytics_events table
      → forward_to_ga4 / forward_to_plausible / forward_to_meta_capi
```

Server-side routes `/api/contact` and `/api/subscribe` on cloudless.gr also
emit events through the same sender.

**Config:**

| Side | Key | Where |
|---|---|---|
| cloudless.gr | `SOCIALAUTO_WEB_ANALYTICS_SECRET` | ssm-config / pod env |
| cloudless.gr | `SOCIALAUTO_WEB_ANALYTICS_URL` (optional override) | pod env |
| SocialAuto | `CLOUDLESS_WEB_ANALYTICS_SECRET` | `.env` (env-fallback config) |
| SocialAuto | `CLOUDLESS_WEB_ANALYTICS_TEAM_ID` | `.env` |
| SocialAuto | `CLOUDLESS_WEB_ANALYTICS_DOMAIN` | `.env`, default `cloudless.gr` |
| SocialAuto | per-tenant `web_analytics_configs` row | alternative to env fallback |

Optional fan-out sinks per config: `GA4_MEASUREMENT_ID`+`GA4_API_SECRET`,
`PLAUSIBLE_*`, `META_PIXEL_ID`+`META_CAPI_ACCESS_TOKEN`.

## (2) Leads — SocialAuto → cloudless.gr → EspoCRM

When a lead is created/upserted in SocialAuto (messenger bots, digital
cards, manual), a fire-and-forget webhook posts it to cloudless.gr, which
creates the lead in EspoCRM.

**Path:**

```
leads.py upsert_lead()/create_lead()
  → _post_cloudless_leads_webhook(payload)
      POST CLOUDLESS_LEADS_WEBHOOK_URL
      header: X-SocialAuto-Webhook-Secret
  → cloudless.gr /api/webhooks/socialauto-leads (route.ts)
      verifies SOCIALAUTO_LEADS_WEBHOOK_SECRET (timing-safe compare)
      → isSocialAutoLead() → toEspoLeadData() → espocrm.createLead()
      campaignSlug: socialauto-<source>
```

**Config:** `CLOUDLESS_LEADS_WEBHOOK_URL` + `CLOUDLESS_LEADS_WEBHOOK_SECRET`
(SocialAuto `.env`) ↔ `SOCIALAUTO_LEADS_WEBHOOK_SECRET` (cloudless.gr
ssm-config). Both set.

## (3) Datalake export — SocialAuto → R2 → cloudless.gr dashboard

The heaviest data flow. SocialAuto snapshots its operational tables into
the shared Cloudflare R2 `datalake-bucket`; cloudless.gr's ETL
materializes them into the admin datalake dashboard.

**Path:**

```
Celery export_datalake  (beat: every 6h at :10, default queue)
  → R2 datalake-bucket, snapshot-style overwrite:
      lake/socialauto-accounts/accounts.json
      lake/socialauto-posts/posts.json
      lake/socialauto-post-metrics/metrics.json
      lake/socialauto-followers/followers.json
      lake/socialauto-account-events/events.json
      lake/socialauto-leads/leads.json          (sha256 email hash only)
      lake/socialauto-web-events/events.json    (no client_ip/user_agent)
      lake/socialauto-insights/<team_id>.json   (insights engine output)

cloudless.gr scripts/etl/materialize-datalake-snapshots.mjs
  → reads lake/socialauto-* + insights
  → builds sections: socialauto_ops, social_engagement, social_outliers,
    social_recommendations, social_leads, social_attribution
  → writes lake/snapshots/admin-datalake.json

admin dashboard /admin/analytics/datalake renders the sections
```

**Config:** `DATALAKE_R2_BUCKET` (default `datalake-bucket`) +
`R2_ACCESS_KEY_ID`/`R2_SECRET_ACCESS_KEY`/`R2_S3_ENDPOINT` on SocialAuto;
the same Cloudflare account serves both sides. PII is minimized at export
(hashed emails, no IPs/UAs).

## (4) Email reports — SocialAuto → @cloudless.gr mailbox

Daily digests and the 21:00 Athens strategy brief are delivered to the
self-hosted cloudless.gr mail domain:

```
social-worker → SMTP omv-ha postfix → Resend relay
  → Cloudflare Email Routing → dovecot IMAP → tbaltzakis@cloudless.gr
```

Config: `SMTP_FROM=noreply@cloudless.gr`, `DIGEST_EMAIL_TO`,
`STRATEGY_REPORT_HOUR=21`. Slack `#socialauto` digests are a separate
SocialAuto-internal channel.

## Edge / access model

- `social.cloudless.gr` sits behind **Cloudflare Access** SSO (app
  `socialauto-app`, admin emails only). Third-party-reachable paths get
  dedicated **bypass apps** scoped to the path prefix (Messenger/WhatsApp/
  Telegram webhooks, OAuth callbacks, health, billing). See
  `.devin/skills/cloudflare-access-paths/`.
- `cloudless.gr` webhooks are publicly reachable; they authenticate via
  shared-secret headers / HMAC (leads) or provider signatures (Stripe,
  LinkedIn `X-LI-Signature`).

## Status (verified 2026-09-21)

| Pipe | Direction | State |
|---|---|---|
| Leads webhook | SocialAuto → cloudless.gr | ✅ Wired both ends; 0 leads sent so far |
| Datalake export | SocialAuto → R2 → dashboard | ✅ Scheduled every 6h; ETL + dashboard consume it |
| Email reports | SocialAuto → mailbox | ✅ Working (strategy brief delivered) |
| Web analytics | cloudless.gr → SocialAuto | ✅ Working — events landing in `web_analytics_events` (first seen 2026-09-21) |

**Web-analytics fixes applied:**

1. `socialauto-analytics-server.ts` default URL now points at the real
   mount `/api/v1/analytics/web/webhooks/cloudless-analytics`.
2. The webhook path is publicly reachable through Cloudflare Access
   (returns 422 on invalid payloads, not a 302/403 gate).
3. SocialAuto side configured via `CLOUDLESS_WEB_ANALYTICS_DOMAIN`,
   `CLOUDLESS_WEB_ANALYTICS_SECRET`, and `CLOUDLESS_WEB_ANALYTICS_TEAM_ID`
   env vars on `social-api` + workers.

## Related but separate

- `cloudless.gr/api/webhooks/linkedin-leads` — LinkedIn Lead Gen Forms →
  cloudless.gr direct (`X-LI-Signature` + `LINKEDIN_CLIENT_SECRET`), not
  through SocialAuto.
- n8n `cloudless-cf-carousel-linkedin` — generates LinkedIn carousel
  content via Cloudflare Workers AI and posts through SocialAuto's API.
