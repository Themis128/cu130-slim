---
name: offer-survey-ops
description: >
  The Cloudless SMB offer-validation survey — 7 questions that test problem
  recognition, vendor pain, price tolerance, objections, and free-audit
  uptake, sent as a SocialAuto `kind:"survey"` email campaign with hosted
  per-recipient `/s/{token}` forms and EspoCRM write-back. Use when
  validating/repricing the signature offer, drafting the survey campaign,
  reading responses, or feeding results into YES-Y offer decisions.
  Tool: scripts/offer_survey.py.
---

# Offer Survey Ops — validate before you sell

Source: owner homework 2026-10-27 (`OneDrive/Kakkava Sofia/Back 2 business/
Homework - Offer + Code of Honor - Baltzakis Themistoklis.md`).
Depends on `email-campaigns-ops` (campaign/survey mechanics) and feeds
`signature-offer-ops` (the offer the survey validates).

## The 7 questions → what each answers

| # | Question (summary) | Type | YES-Y leg |
|---|---|---|---|
| 1 | Who do you call when IT breaks, and how fast is it fixed? | text | problem (YES #2) |
| 2 | Do you sell online? If not — what stopped you? | choice | problem (YES #2) |
| 3 | How many vendors touch your digital presence? | choice | problem (YES #2) |
| 4 | What about your online presence embarrasses you? | text | emotional pain |
| 5 | What would "one partner for all of it" be worth per month? | choice | rate (YES #4) — **the pricing answer** |
| 6 | What would make you hesitate to pay upfront? | text | objection the "why me" must answer |
| 7 | Free 30-min audit — yes/no? | choice | funnel entry |

Read-out rule: Q5 sets the price anchor, Q6 writes the objection-handling,
Q7 = the pipeline. Recipients are token-identified, so a "Yes" on Q7 is a
bookable lead — follow up in EspoCRM.

## Tool — `scripts/offer_survey.py`

```bash
cd .devin/skills/offer-survey-ops/scripts

python3 offer_survey.py questions            # dump the questions JSON
python3 offer_survey.py draft                # create the survey campaign (draft)
python3 offer_survey.py audience <id> --espocrm        # + CRM Leads/Contacts
python3 offer_survey.py audience <id> --email a@b --email c@d
python3 offer_survey.py responses <id>       # aggregate + per-question summary
python3 offer_survey.py send <id> --yes      # real send (explicit --yes required)
```

- Same auth/transport contract as `cross-promo-drip/scripts/drip.py`:
  `SOCIAL_ADMIN_EMAIL`/`SOCIAL_ADMIN_PASSWORD` from repo `.env`,
  `SOCIAL_API_URL` (default `http://127.0.0.1:8083`), no cross-origin
  redirects, refuses cleartext non-localhost.
- `draft` is idempotent per day: if a draft named `SMB offer validation
  (YYYY-MM-DD)` already exists it prints the id instead of duplicating.
- The body uses the `{{survey_url}}` merge tag in a `CTA:` line → branded
  pill button with each recipient's tokenized link. Keep that token literal
  in the body; the send-time fallback appends a bare link otherwise.
- `send` respects the standing gates: 30-day dedup + suppression list apply
  server-side; `DAILY_SEND_BUDGET=80`.

## Rules

- **Draft first, human sends** (Code #2): agents create the draft + audience;
  the owner reviews and sends (or explicitly instructs `send --yes`).
- Voice is mandatory — subject is the hook (<60 chars), one point, one CTA
  (`email_formula` in `brand_voices.voice_signature`).
- Never export raw recipient lists; `responses` prints aggregates + emails
  only for Q7=Yes follow-ups, which the owner acts on in EspoCRM.
