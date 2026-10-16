---
description: >-
  SocialAuto email campaigns + survey funnel wired to EspoCRM — create/audience/send,
  suppression list, per-recipient unsubscribe + survey tokens, datalake export,
  IntraVox feeds. Use when sending marketing/survey email to CRM clients,
  debugging campaign sends, or wiring client-catalog widgets.
---

# Email campaigns & survey funnel (EspoCRM-connected)

## Architecture

- `POST /api/v1/email-campaigns` → `/audience` → `/send` (Celery task
  `app.worker.tasks.email_campaigns.send_campaign`).
- Audience sources: explicit `[{email,name}]`, SocialAuto `leads` (filter by
  `lead_sources`/`lead_interests`), or `include_espocrm` (CRM Leads+Contacts).
- Sends are **per-recipient** messages — never BCC. Each carries
  `List-Unsubscribe` + `List-Unsubscribe-Post` (RFC 8058) and a unique
  unsubscribe token URL.
- Suppression (`email_suppressions`, per-team do-not-contact) is checked at
  queue time AND per send; `unsubscribed`/`skipped_suppressed` are terminal.
- **No-duplicate rule** (`find_duplicate_campaign` in
  `app/services/email_campaigns.py`): subject+body compared with the post
  detector (`similarity_score`, threshold 0.48) against campaigns
  sending/sent in the last 30 days. `POST /send` → 409, worker send →
  `error: duplicate_campaign`. Re-sending the same campaign to new
  recipients is unaffected (self is excluded).
- Daily budget: `DAILY_SEND_BUDGET=80` in `app/services/email_campaigns.py`
  (Resend free tier ~100/day shared with transactional mail) + 2 s pacing.

## Voice — Sofia Kakkava (mandatory)

Every campaign is written in the Visibility Era voice, stored in
`brand_voices.voice_signature` and injected into `/api/v1/ai/generate-content`:

- `email_formula` — subject line IS the hook (recognizable situation, gap,
  never reveals the answer, <60 chars) → opening that makes them feel seen →
  ONE point fully explained → ONE CTA (≤2 lines, specific question/direction).
- `yes_followup_rules` — every follow-up has a job: RECONNECT / CLARIFY /
  ADVANCE. Never "just checking in", no fake urgency, make "no" safe.
- `presold_jobs` — each email does ONE of AUTHORITY / PROBLEM / OFFER /
  CONFIDENCE; campaign rhythm Mon=authority, Wed=problem, Fri=offer,
  Sun=confidence.
- `offer_stack` — H.O.T. model: problem / outcome / mechanism / proof / fit.

Enforced by `PLATFORM_GUIDES["email"]` (`app/services/content_guides.py`) +
an email structure hint in `generate_content`. UI: the "Draft with AI"
button on `/campaigns` calls generate-content with `platform="email"` and
splits `content` on the `SUBJECT:` contract. Source PDFs:
`Yes maxxing/` (follow-up + pre-sold vaults), `Back 2 business/` (H.O.T.
offer), `3 week/` (hooks, blueprint, show-don't-tell) under the OneDrive
Kakkava dir.

## EspoCRM access

- `app/services/espocrm_client.py` — `X-Api-Key` auth, `searchParams` JSON
  (`select` is a **JSON array**, not comma string), offset pagination.
- Env: `ESPOCRM_BASE_URL=http://192.168.1.200:30700` (k3s NodePort — the
  public `espocrm.cloudless.gr` is behind Cloudflare Access),
  `ESPOCRM_API_KEY` = dedicated `socialauto` API user (id `sa9f2e1c4b8a730d6`,
  role cloned from `cloudless-app`). Key lives only in `.env`.
- `crm/summary` + `crm/clients` endpoints prove read access live.
- Write-back: `add_lead_note()` posts a `type:"Post"` Note — body goes in
  the **`post`** field (NOT `note` — silently dropped otherwise).

## Survey funnel

- `kind:"survey"` + `questions` on create → stored in `campaign.meta_data`.
  Question: `{id,label,type:text|choice|rating,options?,required?,min?,max?}`.
- If `survey_url` is empty, each recipient gets a tokenized hosted URL
  `/api/v1/email-campaigns/surveys/{token}` (GET renders form, POST accepts
  form-encoded or JSON). Set `survey_url` to a Nextcloud Forms public link
  to use Forms as the frontend instead.
- On submit: `survey_responses` row (unique per recipient → idempotent),
  local `leads.notes` + `meta_data.last_survey_*` update, EspoCRM Note when
  `meta_data.espocrm_lead_id` is present.
- Staff view: `GET /{campaign_id}/responses`.

## Datalake + IntraVox

- `lake/socialauto-email-campaigns/campaigns.json` on the 6 h
  `export_datalake` — hashed emails (`_email_hash`), recipients, responses.
- Feeds (X-Service-Token): `/api/v1/service-feed/clients` (CRM catalog, no
  raw emails — CRM deep links only) and `/service-feed/campaigns`.
- IntraVox connections `socialauto-clients` / `socialauto-campaigns` on
  `cloud.cloudless.gr`; widgets on *EspoCRM & Contacts* + *Campaigns* pages.
  NOTE: `POST /apps/intravox/api/settings/feed-connections` replaces the
  entire list — always include all existing connections.

## Testing

- `pytest tests/unit/test_email_campaigns.py` — dedupe, suppression,
  synthetic-address exclusion, survey validation, idempotent resubmit.
- E2E done 2026-10-16: self-recipient campaign delivered to
  `tbaltzakis@cloudless.gr`, unsubscribe + survey + CRM Note verified.
- Never send a real campaign without explicit authorization + consent.
