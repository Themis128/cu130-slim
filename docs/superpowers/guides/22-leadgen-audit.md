# Lead-generation readiness audit

SocialAuto ships a leadgen readiness check implementing the machine-testable
parts of Meta's "10 strategies for social media lead generation": are the
capture lanes actually wired — surfaces up, DMs answered, CTAs tagged,
leads landing in the CRM.

## Run the audit

```bash
docker exec social-api python scripts/leadgen_audit.py          # human report
docker exec social-api python scripts/leadgen_audit.py --json   # digests/automation
```

Exit `1` when a capture surface is down, an active account has no DM
lead-capture lane, or UTM discipline is broken.

## What it checks

- **Capture surfaces** — live HTTP probes on `cloudless.gr/contact`
  (→ EspoCRM lead), `social.cloudless.gr/pricing` (→ Polar), and
  `cloudless.gr/links` (the 12-Automation Checklist lead magnet).
- **DM lead lanes** — per active account, the platform's
  `*_auto_reply`/bot flag in `social_accounts.meta_data`. A DM is a lead
  signal; an account that can't answer DMs is a dead capture lane.
- **Profile funnel links** — where a bio/website is cached in `meta_data`
  (TikTok), verifies it points at `cloudless.gr`. "bio not cached" is an
  honest unknown, not a failure.
- **Attribution loop** — posts carrying a `cloudless.gr` link are
  UTM-tagged automatically at publish (`app/services/utm.py`); the audit
  measures the outcome: `web_analytics_events` with a `utm_source`
  payload. Tagged inventory with zero attributed events in 30d = gap.
- **Lead volume (30d)** — EspoCRM `Lead` count by `source` — Meta's "lead
  volume" metric measured in the CRM, not platform dashboards.

## Act on the result

- Surface down → fix site/tunnel first; every CTA dead-ends while it's down.
- `MISSING` DM lane → enable auto-reply for that account (channel skill).
- Attribution silent → tagged links exist but no site events land:
  check the site/tunnel, or whether posts carry a real CTA link.
- All green → the capture machinery works; move to `funnel-ops` (weakest
  stage) and `trendwatch-ops` (topics).

Out of scope by design: Meta lead ads, retargeting, Advantage+ — paid-only
features under the no-spend rule.
