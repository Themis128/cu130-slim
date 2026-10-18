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
| Clean | `services/crm_hygiene.py` — weekly Monday 07:30 sweep: dedupe by normalized email (newest wins, loser → `Dead` + audit note), quarantine `ci-test+*`/`*@example.*` probes, stale-New SLA list | live |
| Score | `scoreLead` (cloudless.gr `lead-scoring.ts`) on contact-form leads — fit+intent, `hot` band auto-opens Opportunity `qualifiedtobuy` | live on contact path |
| Segment | Lead `source` enum + campaign slug / `utm_*` lines in description; campaign audiences via `espocrm_client.list_leads_for_campaign()` (excludes Dead/Converted, needs email) | live |
| Route | `Lead.create` webhook → Slack `#leads`; stale-New >7d SLA list in the weekly report | live |
| Lifecycle | `enrollLeadInAutomation` (ActiveCampaign) on contact-form submit; survey/campaign loops via `email-campaigns-ops` | live |
| Closed loop | weekly Slack report (totals by status/source, quarantined, deduped, stale) from `crm_hygiene`; `lead_counts()` entity totals | live |

## Rules

- **Never quarantine `psid.*@messenger.local`** — synthetic but real
  Messenger-sourced leads. Only `ci-test+*`, `*@example.invalid|com`,
  `test@test` are probes.
- Dedupe is **non-destructive**: loser → `status: Dead` + Note pointing at
  the keeper id. Nothing is deleted; every action is reversible in the UI.
- New leads have a **7-day follow-up SLA** — the sweep names them in the
  Slack report rather than silently aging.
- UTMs travel in `Lead.description` (`utm_source:` etc. lines) + the
  `campaign:` slug — `countLeadsForCampaign` aggregates on it.

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
- Backend route `app/api/leads.py` — `/api/v1/leads/public` ingest
  (Cloudflare Access service token) + manual leads
- `src/lib/espocrm.ts` (cloudless.gr) — Contact/Deal/Note upserts,
  `createLead` with email dedup + UTM description
