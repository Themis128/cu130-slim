# Monthly checklist update (PDF → social announce)

On the **1st of each month**, refresh the **12-Automation Checklist**
lead-magnet PDF served at `cloudless.gr/links` → `automation-checklist.pdf`,
deploy it via a PR on `Themis128/cloudless.gr`, then publish a platform-adapted
announcement to every connected social account.

Full runbook: `.devin/skills/monthly-checklist-update/SKILL.md`.

## How it works

```
1st of month (or manual trigger)
  └─ Preflight: ensure ~/cloudless.gr checkout exists and is clean
  └─ Update scripts/generate_automation_checklist.py
        refresh CHECKLIST items, TOOLS, edition month/year, CTA
  └─ Regenerate PDF inside social-api (has reportlab)
  └─ PR on cloudless.gr: script + public/automation-checklist.pdf
  └─ Announce on all 6 accounts via SocialAuto
        /api/v1/ai/generate-content → /api/v1/content/posts → /publish-now
```

## Publish targets

| Platform          | Account ID                               | Notes |
|-------------------|------------------------------------------|-------|
| LinkedIn Company  | `9c4451bb-e820-489f-8676-76ddbc788ffe`   | `link_url` → `/links` |
| LinkedIn Personal | `18d5cd59-f0c2-4fc4-986e-03601734c7a5`  | `link_url` → `/links` |
| Threads           | `1071dcd5-1bc9-4770-923c-d897eb124485`   | URL in text |
| Twitter/X         | `a89d6852-eff8-479a-835f-50d806cf59dd`   | URL in text — `link_url` is dropped on X |
| Instagram         | `38ddbd44-8811-4d0b-be62-a23fd2f50490`   | Requires a generated image; "link in bio" |
| Facebook Personal | `9355ed63-7787-43e5-a22d-ae0a33d5176b`  | Browser-sidecar publish; `link_url` → `/links` |

## Gotchas

- **Twitter loses `link_url`** — `render_post_text` appends it only for
  LinkedIn/Facebook. The tweet text must literally contain
  `https://cloudless.gr/links` and stay under 280 chars.
- **Instagram requires media** — text-only posts are `skipped`. Generate an
  image via `/api/v1/ai/generate-image` and attach it.
- **Destination is `/links` on purpose** — this campaign promotes the
  checklist lead magnet, overriding the standard funnel CTAs
  (`/contact`, `social.cloudless.gr/pricing`) for this campaign only.
- **CI formatting** — cloudless.gr CI runs `ruff format --check`; format the
  Python generator before committing.
- **Facebook personal** publishes through the browser sidecar, not Graph API —
  allow ~60s before verifying `status=published`.

## Verify

1. `curl -sI https://cloudless.gr/automation-checklist.pdf` → `200`, fresh
   `Last-Modified`.
2. All 6 publish targets reach `status=published` with platform URLs.
   Instagram's permalink needs Graph API resolution (stored URL is numeric).
