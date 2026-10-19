# Sales-funnel audit

SocialAuto ships a live sales-funnel audit implementing the quantitative
funnel model of Tolstova & Silakova (Omsk Scientific Bulletin 2023): every
stage has a real headcount, conversion is measured stage-to-stage, and the
weakest transition is named so effort goes to the broken link — not to
buying more traffic.

## Run the audit

```bash
docker exec social-api python scripts/funnel_audit.py          # human report
docker exec social-api python scripts/funnel_audit.py --json   # digests/automation
```

Exit code is `1` when any stage is at zero while the stage before it has
volume — a structurally broken transition, not noise.

## Read the report

```
stage          count  stage→stage conv
audience        1006           followers, latest per account (N 30d ago)
engaged          154  (proxy)  posts with engagement, 30d — activity proxy
lead             179  116.23%  EspoCRM leads (8 dead)
nurtured           0  0.0%     leads past 'New' + campaign sends
opportunity        0           EspoCRM opportunities, active only
won                0           opportunities closed-won

weakest transition: lead→nurtured (0.0%)
```

- **audience** — summed latest `follower_snapshots` per account; the 30d-ago
  figure makes the auction warning a measured-growth check.
- **engaged** — unique posts with likes+comments+shares in 30d
  (`coalesce(post_id, platform_post_id)` covers discovered posts). It is an
  **activity proxy, not engaged people** — analytics snapshots carry no actor
  identity, so its percentage is shown for context but never diagnosed as the
  weakest link.
- **lead / nurtured / opportunity / won** — EspoCRM counts. `nurtured` =
  leads past `New`/`Dead`; `opportunity` = active deals only (closed-lost and
  closed-won excluded); `won` = closed-won. `email_recipients` with
  `status='sent'` is the real drip volume — `email_logs` is transactional.

## Act on the result

- **weakest transition** — fix that stage first; see the `funnel-ops` skill
  for the dispatch table (e.g. `lead→nurtured` → check `crm-lifecycle`
  worker + the LinkedIn DM lane for email-less leads).
- **auction warning** — fires only when 30d follower growth is measured AND
  lead conversion vs audience is under 0.5%. Do not raise traffic/spend
  while it fires (each incremental visitor costs more than the last).
- **lead sources** — tells you which capture lane feeds the funnel
  (Newsletter / Web Site / Messenger), so nurture fixes target the right
  audience.

## Cadence

Run daily alongside the strategy brief, or on demand before any traffic or
spend decision. Pair with `workforce_audit.py` (is the nurture worker even
running?) and `trendwatch.py` (which topics to feed the top of the funnel).
