---
name: espocrm-ops
description: >-
  Data-driven EspoCRM operations for cloudless.gr — the adopted
  capture→clean→score→segment→route→lifecycle→closed-loop discipline, the
  weekly crm_hygiene Celery sweep, lead ingress paths, dedup/quarantine
  rules, follow-up SLA, and the espocrm_client read/write surface. Use when
  working leads/contacts/opportunities, CRM hygiene, funnel attribution,
  campaign audiences, or "is the CRM loop working".
allowed-tools:
  - read
  - exec
  - grep
  - glob
triggers:
  - user
  - model
---

# EspoCRM ops — data-driven CRM

EspoCRM runs on omv k3s (`espocrm` namespace). Public `espocrm.cloudless.gr`
sits behind Cloudflare Access — the backend reaches the NodePort
`http://192.168.1.200:30700` with the `socialauto` API user (`ESPOCRM_*` env).

## The adopted loop (from the data-driven CRM playbook)

| Stage | Where it lives | State |
|-------|---------------|-------|
| Capture | `cloudless.gr/contact` → `espocrm.ts` Contact+Note (score, NLP, UTM) + hot→Opportunity; `playbook-lead` → SocialAuto `/api/v1/leads/public` → `createLead` (email dedup + UTM in description); Messenger → Lead (`psid.*@messenger.local` — real leads, never quarantine) | live |
| Clean | `services/crm_hygiene.py` — weekly Monday 07:30 sweep: dedupe by normalized email (newest wins, loser → `Dead` + audit note), quarantine `ci-test+*`/`*@example.*` probes **and `*@cloudless.gr` self/internal addresses**, stale-New SLA list | live |
| Route-fix | same sweep auto-assigns unowned live leads — `CRM_HYGIENE_ASSIGNEE_ID` when set, else the `socialauto` API user (automation owns it, visible in UI) | live |
| Score | `scoreLead` (cloudless.gr `lead-scoring.ts`) on contact-form leads — fit+intent, `hot` band auto-opens Opportunity `qualifiedtobuy`; **all other ingress** scored by `services/crm_lifecycle.score_lead` — daily `crm-lifecycle-sweep` (08:50) mirrors the same model (hot≥65/warm≥35) against EspoCRM fields, writes `Lead score:` note as dedupe marker, re-scores on newer reply | live — all ingress |
| Segment | Lead `source` enum (extended 2026-10-19: `Social Media`, `Meta Lead Ad`, `LinkedIn Lead Gen`, `WhatsApp Flow`, `Messenger`, `Lead Magnet`, `Referral`, `API Ingest` alongside the 9 core values) + `utm_*`/`leadMagnet` native custom fields + TargetLists (app role has full `TargetList` ACL since 2026-10-19; lists: `Checklist downloaders`, `WhatsApp leads`, `Meta lead ads`, `Messenger leads`, `Social engagement (replies)`); campaign audiences via `espocrm_client.list_leads_for_campaign()` (excludes Dead/Converted, needs email) | live |
| Route | `Lead.create` webhook → Slack `#leads`; stale-New >7d SLA list in the weekly report | live |
| Lifecycle | `enrollLeadInAutomation` (ActiveCampaign) on contact-form submit; **hot-lead convert** — lifecycle sweep runs Espo's `Lead/action/convert` (records: Contact+Opportunity, +Account when `accountName`) for `hot` real-email assigned leads, `CRM_CONVERT_ENABLED` gates it; synthetic/test/self never convert | live |
| Engagement | `services/crm_engagement.py` — 30-min `crm-engagement-sweep` polls the dovecot mailbox (IMAP, `SMTP_*`/`PROMO_SCAN_IMAP_*` identity), matches senders to live Lead/Contact emails, attaches `Reply received: <subject> · msg <id>` (msg-id dedupe), bumps replied `New`/`Assigned` leads → `In Process`, **enrols reply-engaged Leads into `Social engagement (replies)`** via `link_lead_to_target_list` (union, never wipes), Slack-alerts only on hits | live |
| Closed loop | weekly Slack report (totals by status/source, quarantined, deduped, stale) from `crm_hygiene`; `lead_counts()` entity totals | live |

## Rules

- **Never quarantine `psid.*@messenger.local`** — synthetic but real
  Messenger-sourced leads. Only `ci-test+*`, `*@example.invalid|com`,
  `test@test` are probes.
- Dedupe is **non-destructive**: loser → `status: Dead` + Note pointing at
  the keeper id. Nothing is deleted; every action is reversible in the UI.
- New leads have a **7-day follow-up SLA** — the sweep names them in the
  Slack report rather than silently aging.
- UTMs live on **native custom fields** (`utmSource/utmMedium/utmCampaign/
  utmContent/utmTerm/leadMagnet` — custom entityDefs + `rebuild.php`,
  deployed 2026-10-19) *and* `Lead.description` lines for back-compat —
  `countLeadsForCampaign` still aggregates on description.
- `createLead` (cloudless.gr `src/lib/espocrm.ts`) writes the native fields
  + `targetListNames`→`targetListsIds` (set-replacing; on dedup-update it
  unions via `/Lead/{id}/targetLists` — the ids attr is NOT `select`-able).
  Re-ingest PUT never sends `status`/empty strings/fallback `lastName`, and
  merges `description` lines rather than replacing. `LeadData.utmTerm` +
  `leadMagnet` + `targetListNames` fields exist for callers.
- `/api/subscribe?magnet=<slug>` creates a `Lead Magnet` lead into
  `Checklist downloaders` — `/links` now carries an opt-in
  `ChecklistSignup` under the PDF link (`magnet=automation-checklist`);
  the PDF itself stays ungated.
- **Meta leadgen path** — the Cloudless Meta app (`1936126137016578`) has
  exactly ONE page webhook callback: `social.cloudless.gr/api/v1/messenger/
  webhook`. `leadgen` was added to the app-level `page` subscription
  (fields list now ends `…feed,leadgen`) and `entry[].changes[]`
  `field=="leadgen"` is ingested by `messenger._ingest_meta_leadgen` —
  post-response `BackgroundTasks` → Graph `GET /{leadgen_id}` with the
  stored Page token → `espocrm_client.upsert_lead` (email dedup, unioned
  lists, merged description, `source="Meta Lead Ad"`) → `Meta lead ads`
  TargetList. **Blocked until the app is granted `leads_retrieval`** —
  page `subscribed_apps` POST returned `(#200) needs leads_retrieval`
  (2026-10-19). Standalone fallback endpoint also shipped: cloudless.gr
  `POST /api/webhooks/meta-leads` (HMAC verify + same CRM write) for a
  dedicated Meta app if one is ever created.
- `espocrm_client.upsert_lead` is the SocialAuto-side twin of
  `createLead` — same non-destructive dedup contract for in-repo ingress
  (meta leadgen, future webhook consumers).
- SocialAuto channel→source map (`socialauto-leads.ts`): messenger DMs →
  `Messenger`, `whatsapp_flow` → `WhatsApp Flow`, `website` → `Web Site`;
  LinkedIn leadgen → `LinkedIn Lead Gen`.

## Surfaces

```bash
# Manual sweep
docker compose exec social-worker-default python -c \
  "import asyncio; from app.services.crm_hygiene import run_crm_hygiene; \
   print(asyncio.run(run_crm_hygiene()))"

# Entity counts / campaign audience profiles (python, inside worker)
espocrm_client.lead_counts() / campaign_profiles() / list_all(entity)
```

- `app/services/espocrm_client.py` — `espo_get/list_all/espo_post/
  espo_patch/add_lead_note` (X-Api-Key, NodePort)
- `app/worker/tasks/crm_hygiene.py` — Celery `run_crm_hygiene`, weekly beat
  `crm-hygiene` (Mon 07:30 Athens, default queue)
- `app/worker/tasks/crm_lifecycle.py` — daily 08:50 `crm-lifecycle-sweep`
- `app/worker/tasks/crm_engagement.py` — every-30-min `crm-engagement-sweep`
- Backend route `app/api/leads.py` — `/api/v1/leads/public` ingest
  (Cloudflare Access service token) + manual leads
- `src/lib/espocrm.ts` (cloudless.gr) — Contact/Deal/Note upserts,
- `deploy/monitoring/espocrm/exporter.py` — Prometheus exporter querying
  espocrm-mariadb directly (`espocrm_*` series; see `monitoring-ops`)
  `createLead` with email dedup + UTM description



### Funnel segment: LinkedIn newsletter (live 2026-10-19)

- Campaign `LinkedIn newsletter` + TargetList `LinkedIn newsletter` hold the
  subscribers of "The Cloudless Build Log" (`newsletters/the-cloudless-build-log-7511859883881914368/`,
  169 subs, personal profile — newsletters are bound to the author).
- Each Lead: `source=Newsletter` (enum added to Lead.source — options REPLACE
  on write, always send the full union), `utmSource=linkedin`,
  `utmMedium=newsletter`, `utmCampaign=linkedin_newsletter`, `campaignId` linked,
  description carries profile URL + headline + connection degree + inbox action.
- Dedup key = `linkedin.com/in/` URL token inside `description` (no email for
  these leads — name matching is unsafe).
- **Not email-campaign eligible** — `list_leads_for_campaign` requires an
  email, so TargetList membership alone does NOT put these leads in
  `/api/v1/email-campaigns` audiences or the drip sweep. Outreach path is
  LinkedIn DM/invite via the sidecar (below), not email — do not add this
  list to an email campaign audience expecting delivery.
- **Scrape procedure** (sidecar can't hydrate the newsletter page — use the
  bridge on :9223, `X-Platform: linkedin`, force the session): navigate the
  newsletter URL → JS-click the "N subscribers" `<button>` → modal
  `reader-subscribers-modal` opens at `/subscribers/` → scroll
  `.artdeco-modal__content` until
  `.reader-subscriber-list-item__actor-profile-link` count == subscriber total
  (~10/scroll) → per `li.reader-subscriber-list__actor-list-item` extract
  `a[href]`, `p.t-bold` (name), `.distance-badge` (degree), second `p`
  (headline), action `button` (Message/Connect/Follow).
- **Communication reality**: `Message` action ≈ reachable via sidecar DM
  (146/169 on first sync); `Connect` needs an invite first; `Follow` is
  non-connection. Mass-DM through the sidecar is rate-limit risky — use
  small batches with the deferred-reply workflow pattern, never a tight loop.
