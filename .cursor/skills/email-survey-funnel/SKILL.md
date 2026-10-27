# Email Survey Funnel — end-to-end loop

The `kind:"survey"` email campaign loop: send → branded per-recipient form →
answers → Slack ping + EspoCRM note → nudge the silent → auto-readout →
audit booking. All surfaces live in `social-automation/backend/app/api/email_campaigns.py`.

## Endpoints

| Route | Auth | Purpose |
|---|---|---|
| `GET /s/{token}` | public | Survey form — numbered cards, pill choices, segmented ratings |
| `POST /s/{token}` | public | Submit — saves response, Slack ping, audit-yes → booking CTA |
| `GET /{cid}/responses` | staff | JSON rows (email, answers, espocrm_noted, submitted_at) |
| `GET /{cid}/responses/view` | staff | HTML dashboard — choice bars, per-email answers, audit-request card |
| `POST /{cid}/nudge` | staff | Flips non-responders back to `pending` for the next send |
| `POST /{cid}/send` | staff | Queues `send_campaign` for pending recipients |

## CLI (`offer_survey.py`)

```bash
python3 offer_survey.py draft                    # create kind:"survey" draft
python3 offer_survey.py audience <cid> --espocrm # pull CRM audience
python3 offer_survey.py send <cid> --yes         # send (human-gated)
python3 offer_survey.py responses <cid>          # aggregate answers
python3 offer_survey.py nudge <cid> [--yes]      # re-queue non-responders
python3 offer_survey.py readout <cid> [--yes]    # auto-draft summary → responders
```

## Gotchas (learned the hard way)

- **Coroutine-scalars bug**: `await db.execute(q)` returns a coroutine — write
  `(await db.execute(q)).scalars().first()`, not `await db.execute(q).scalars().first()`.
- **`docker cp` is not persistent** — files copied into a container vanish on
  restart if they aren't in the image; rebuild or the route 404s.
- **Branch treadmill**: `strict:true` on master + no merge queue — rebase loop
  in `pr-landing-ops` or merge master in. Auto-merge stays armed across pushes.
- **`research` module chain**: `__init__.py` imports `research` which needs
  `services/research_workspace` + `services/lead_research` — all three must exist.
- **Force-push churn**: parallel automation rewrites shared branches — prefer
  a fresh branch name over `--force-with-lease` fights.

## Funnel semantics

- `audit_interest=Yes` → "Book the free 30-min audit" CTA (cloudless.gr/contact,
  which runs the chat booking flow) + `AUDIT YES` Slack flag.
- `audit_interest=No` → "I'll send you the read-out" copy.
- Every submission pings `#socialauto-alerts` via `post_alert_to_slack` —
  fire-and-forget, never blocks the save.
- Responses write back to EspoCRM Notes on the matching lead.

## Tests

`tests/unit/test_survey_funnel_ux.py` — pill/segmented render, numbered cards,
audit-CTA split, nudge flip, dashboard aggregates (8 tests).
`tests/unit/test_email_funnel_escaping.py` — XSS regression on the public pages.
