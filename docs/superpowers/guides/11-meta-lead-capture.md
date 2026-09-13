# Meta lead capture (Cloudless) — WhatsApp Flows, Messenger, Instagram DMs

This guide scaffolds **organic** lead capture for Cloudless (`cloudless.gr`) across:

- **WhatsApp** (Flows)
- **Facebook Page Messenger**
- **Instagram DMs**

All channels capture the same fields:

- `name`
- `email`
- `company_size` (`solo`, `2-5`, `6-20`, `21-50`, `51-200`, `200+`)
- `interest` (`cloud`, `growth`, `audit`)

Captured leads are persisted in the `leads` table and can optionally be pushed to Slack and/or an n8n webhook.

## Shared storage (SocialAuto)

- **List leads**: `GET /api/v1/leads`
- **Create lead manually**: `POST /api/v1/leads`

## WhatsApp — publish the Cloudless lead-capture Flow

Flow JSON file:

- `social-automation/flows/whatsapp/cloudless-lead-capture.json`

### Option A (recommended): create + publish via SocialAuto API

1. Ensure a WhatsApp account is connected in SocialAuto and has:
   - `meta_data.access_token`
   - `meta_data.waba_id`
   - `meta_data.phone_number_id`

2. **Create a draft Flow**:

`POST /api/v1/whatsapp/{account_id}/flows`

Body (example):

```json
{
  "name": "Cloudless Lead Capture",
  "categories": ["LEAD_GENERATION"]
}
```

3. **Upload Flow JSON**:

`PUT /api/v1/whatsapp/{account_id}/flows/{flow_id}/json`

Body:

```json
{
  "flow_json": { "...paste cloudless-lead-capture.json here..." }
}
```

4. **Validate (optional)**:

`POST /api/v1/whatsapp/{account_id}/flows/{flow_id}/validate`

5. **Publish**:

`POST /api/v1/whatsapp/{account_id}/flows/{flow_id}/publish`

### Option B: WhatsApp Manager UI

Use WhatsApp Manager to create a Flow and paste the JSON from
`social-automation/flows/whatsapp/cloudless-lead-capture.json`, then publish.

### Lead ingestion

When the user completes the Flow, WhatsApp sends a Flow completion message to the normal webhook:

- `POST /api/v1/whatsapp/webhook`

SocialAuto detects Flow completion payloads containing `name` + `email` and persists a `Lead` with:

- `source=whatsapp_flow`
- `thread_id=<sender_phone>` (best-effort)

No Lead Ads scopes are required (organic messaging only).

## Facebook Page Messenger — ice breakers + lead capture

### Profile + ice breakers

Use the existing Messenger profile endpoints:

- `POST /api/v1/messenger/{account_id}/setup`
- `PUT /api/v1/messenger/{account_id}/profile`

Recommended **Ice Breakers** (Messenger Profile `ice_breakers` property):

- “Free audit”
- “Cloud setup”
- “Growth / marketing”

These are user-sent message texts; SocialAuto starts lead capture on common keywords like `audit`, `contact`, `demo`, etc.

### Lead capture behavior

Messenger lead capture is a simple state machine (Redis-backed) that runs **before** the AI auto-reply:

- Starts on `GET_STARTED`, “Contact”, or keyword triggers
- Asks for name → email → company size → interest
- Persists a `Lead` with `source=facebook_messenger`

## Instagram — DM keyword lead capture

Instagram DMs are handled via the existing poller task:

- `app.worker.tasks.instagram_messenger.poll_instagram_messenger`

Lead capture is triggered by the same keyword set (e.g. `audit`, `contact`, `demo`) and persists leads as:

- `source=instagram_dm`

Note: Instagram Messaging API requires `instagram_business_manage_messages` permission (App Review).

## Facebook Page CTA → Messenger handoff

For organic capture, prefer **Page CTA** that opens Messenger (or WhatsApp) rather than Lead Ads:

- Set Page CTA button to **Send Message**
- Put “Free audit” in the CTA description
- The first interaction hits `GET_STARTED`, which triggers lead capture

## Optional: Slack + n8n notifications

Environment variables (all optional):

- `LEAD_CREATED_WEBHOOK_URL`: POST a `lead.created` JSON payload to an external workflow (ideal for n8n).
- `CLOUDLESS_LEADS_WEBHOOK_URL`: POST a `lead.upserted` JSON payload (create or update) to the Cloudless app.
- `CLOUDLESS_LEADS_WEBHOOK_SECRET`: sent as `X-SocialAuto-Webhook-Secret` when posting to `CLOUDLESS_LEADS_WEBHOOK_URL`.
- `SLACK_LEADS_WEBHOOK_URL`: Incoming Slack webhook for lead notifications.
- `SLACK_LEADS_CHANNEL_ID`: Slack channel id when posting via token (fallback).

If `SLACK_LEADS_WEBHOOK_URL` is not set, lead notifications fall back to `SLACK_WEBHOOK_URL` / `SLACK_CHANNEL_ID`.

