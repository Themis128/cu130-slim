# Survey Funnel Ops — the 60-second stack audit

## The funnel

```
Social post → cloudless.gr/survey → /api/survey (Next.js)
  → SocialAuto /api/v1/leads/public/survey (CF Access service token)
  → create_lead (local) + espo upsert → nurture_sequence day 3/7/14
```

## Endpoints

- `GET cloudless.gr/survey` — the public 3-question form (stack, pain, team size)
- `POST /api/survey` — forwards to SocialAuto, honeypot + email validation
- `POST /api/v1/leads/public/survey` — SocialAuto side (rate-limited 10/min)

## Posting templates (per platform, real voice)

- **LinkedIn** (company page `9c4451bb-...`): "Where does your stack actually run? 60-second audit → cloudless.gr/survey"
- **Threads/IG**: same hook, conversational — the questions ARE the engagement
- **X**: pain-first — "biggest bottleneck: cost, perf, monitoring, or security?"
- **Facebook Page**: survey + playbook CTA ("get the migration playbook")

## Tools

- `survey-stats.sh` — survey-lead count in EspoCRM (source=Newsletter, form=survey)
- `post-survey-link.sh <platform>` — post the survey link via SocialAuto publish

## Funnel checks

- Survey leads land in `leads` with `meta.form=survey` + answers JSON
- EspoCRM upsert adds company_size/phone — richer than the bare public lead
- The `nurture_sequence` sweep (day 3/7/14) picks them up automatically
