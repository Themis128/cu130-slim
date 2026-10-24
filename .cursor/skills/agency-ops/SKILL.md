# Agency Ops — the autonomous-agency pipeline

The agent-led fixed-scope agency (cloudless.gr model): inbound lead →
triage → site audit → priced proposal → 14-day project, run by four
governed workers on the SocialAuto stack. Read `social-content-core` for
the shared data-driven mandate; this skill is the pipeline map.

## The four agents (`app/services/agency_pipeline.py`)

| Worker | Task | Cadence | Domain | Autonomy |
|---|---|---|---|---|
| agency-front-desk | `agency.run_agency_triage_sweep` | */15 min | classify leads → service catalog, queue audits | auto (writes notes/rows only) |
| agency-architect | `agency.run_agency_audit_sweep` | */10 min | deterministic site audit of `agency_audits` pending rows | auto (measured data only) |
| agency-proposals | `agency.run_agency_proposal_sweep` | */30 min | completed audit → priced proposal draft | **review** — Slack approve/reject gate |
| agency-pm | `agency.run_agency_pm_sweep` | daily 09:20 | 14-day milestone loop on `agency_projects` | **review** — client updates are drafts |

## Flow

```
Lead (EspoCRM + local leads) 
  → triage: classify_services() → services + domain → AgencyAudit(pending)
  → audit: site_audit.run_site_audit(url) → score/findings/action_plan → EspoCRM note
  → proposal: render_proposal() → AgencyProposal(draft) → Slack approve/reject
  → approve → send_proposal() emails the lead (only path that mails anyone)
  → won → POST /api/v1/agency/projects → pm_sweep drafts day-1/5/10/14 updates,
    compiles handoff at day 14, stalls flag >2d overdue
```

## Data tables (`app/models/agency.py`)

- `agency_audits` — url, status, score, findings[], recommended_services[],
  action_plan; `espo_lead_id` links CRM-native leads (contact-form path).
- `agency_proposals` — services[], price_low/high_eur, subject, body_md,
  status draft→approved/rejected/sent; approve is the human gate.
- `agency_projects` — kickoff_at/due_at, milestones[] {day,key,label,done},
  handoff_md, last_client_update_at.

## Catalog (`app/services/agency_catalog.py`)

Four services €800–€2,400 each, €3,600/mo full bundle, 14-day delivery —
edit `SERVICES`/`BUNDLE_PRICE_EUR` when public pricing changes.
`classify_services` is deterministic (en+el keywords, explainable hits);
`extract_domain` finds the lead's site (URL → bare domain → email domain),
skipping cloudless.gr and free-mail hosts.

## The Architect (`app/services/site_audit.py`)

Deterministic checks, no fabricated metrics: HTTPS/TLS, security headers
(HSTS/CSP/nosniff/referrer/frame), X-Powered-By exposure, TTFB bands,
compression, tech fingerprint (WordPress/Next/Shopify/…), analytics
presence (GA4/GTM/Plausible/Meta), SEO basics (title/desc/viewport/h1/
lang/canonical/robots/sitemap). SSRF-guarded via `url_safety`. Score starts
at 100, −15/−6/−2 per critical/warning/info. Findings map to services.

## API (`app/api/agency.py`, admin-only)

```
GET  /api/v1/agency/catalog                     — services + prices
GET  /api/v1/agency/audits[?status=]            — list
POST /api/v1/agency/audits {url,lead_id?}       — synchronous manual audit
GET  /api/v1/agency/proposals[?status=]         — list
GET  /api/v1/agency/proposals/{id}              — full body_md
POST /api/v1/agency/proposals/{id}/approve      — THE SEND GATE (emails lead)
POST /api/v1/agency/proposals/{id}/reject
GET  /api/v1/agency/projects[?status=]          — list
POST /api/v1/agency/projects {name,proposal_id?} — kick off 14-day plan
GET  /api/v1/agency/projects/{id}               — incl. handoff_md
```

## Slack approval lane

Proposal alerts post to #socialauto-alerts with `socialauto_proposal_approve`
/`socialauto_proposal_reject` buttons → cloudless.gr
`/api/slack/interactions` → `runOpsAction` → the API above (admin JWT via
`SOCIALAUTO_ADMIN_*`). Mutations stay behind `SLACK_OPS_USERS` allowlist.

## Settings

- `AGENCY_ENABLED` (default true) — kills all four sweeps + API writes.
- `AGENCY_AUDIT_MAX_PER_RUN` (default 5) — bound outbound fetches.
- `AGENCY_PROPOSAL_AUTOSEND` (default **false**) — reserved for the
  earned-autonomy ladder; do not flip without ≥10 clean proposal audits.

## Dedupe contract

- Triage: `lead.meta_data.agency_triaged` (local) / `Agency triage:` note
  (EspoCRM) / existing `agency_audits` row per lead.
- Proposals: one per `audit_id`.
- Audits: `status=pending` rows only; failures park as `failed` + error.

## Verify

```bash
docker exec -w /app social-api python scripts/workforce_audit.py --governance
docker compose exec -T social-api python -m pytest tests/unit/test_agency.py -q
curl -s http://localhost:8083/api/v1/agency/catalog -H "Authorization: Bearer $TOKEN"
# manual audit
curl -X POST http://localhost:8083/api/v1/agency/audits \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"url":"https://example.gr"}'
```

## Honest gaps

- Implementation agents (code-gen → GitHub PRs) are NOT built — the PM
  loop tracks milestones but engineering work is still human/Devin.
- Triage classification is keyword-deterministic; an LLM refine step can
  sit on top but must keep the explainable-hits note.
- Proposal email is plain markdown text (no PDF) — upgrade when volume
  justifies it.
