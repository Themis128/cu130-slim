# Monthly Checklist PDF Update & Social Announce

Update the **12-Automation Checklist** lead-magnet PDF on `cloudless.gr/links`
and announce it across all social channels. Run on the **1st of each month**
(or when the user asks to refresh the checklist).

## What it does

1. **Review current services** — fetch `cloudless.gr/en/services` to confirm
   the service lineup and pricing haven't changed.
2. **Update the generator script** — edit
   `cloudless.gr/scripts/generate_automation_checklist.py`:
   - Refresh `CHECKLIST` items to reflect current production workflows.
   - Refresh `TOOLS` list if the stack changed.
   - Update the edition month/year string.
   - Update the CTA to match current services and pricing.
3. **Regenerate the PDF** — run inside `social-api` (has `reportlab`):
   ```bash
   docker cp cloudless.gr/scripts/generate_automation_checklist.py \
     social-api:/app/scripts/generate_automation_checklist.py
   docker exec social-api python /app/scripts/generate_automation_checklist.py \
     /tmp/automation-checklist.pdf
   docker cp social-api:/tmp/automation-checklist.pdf \
     cloudless.gr/public/automation-checklist.pdf
   ```
4. **Deploy via PR** — branch, commit both files (script + PDF), push, open PR
   on `Themis128/cloudless.gr`, wait for CI, merge.
5. **Post to all channels** — generate platform-adapted copy through
   SocialAuto AI pipeline (`/api/v1/ai/generate-content`) and publish via
   `/api/v1/content/posts` + `/publish-now`:

   | Platform         | Account ID                               | Notes |
   |------------------|------------------------------------------|-------|
   | LinkedIn Company | `9c4451bb-e820-489f-8676-76ddbc788ffe`   | Corporate value, `link_url` to `/links` |
   | LinkedIn Personal| `18d5cd59-f0c2-4fc4-986e-03601734c7a5`  | Founder voice, `link_url` to `/links` |
   | Threads          | `1071dcd5-1bc9-4770-923c-d897eb124485`   | Casual, direct |
   | Twitter/X        | `a89d6852-eff8-479a-835f-50d806cf59dd`   | Under 280 chars, `link_url` to `/links` |
   | Instagram        | `38ddbd44-8811-4d0b-be62-a23fd2f50490`   | **Requires image** — generate via `/api/v1/ai/generate-image` first |
   | Facebook Personal| `9355ed63-7787-43e5-a22d-ae0a33d5176b`  | Build-log voice, `link_url` to `/links` |

6. **Verify** — confirm all 6 targets reach `status=published` with URLs.
   Instagram permalink needs Graph API resolution (stored URL uses numeric ID).

## Gotchas

- **Instagram requires media** — text-only posts are `skipped`. Always attach
  an AI-generated image asset.
- **Twitter char limit** — keep body under ~250 chars; hashtags and link add
  more. The 500 error from SocialAuto is a post-creation FK violation if the
  account ID is wrong — the correct Twitter ID is
  `a89d6852-eff8-479a-835f-50d806cf59dd`.
- **ruff format** — CI runs `ruff format --check`; always format the Python
  script before committing.
- **Facebook personal** posts go through the browser sidecar, not Graph API.
  Publishing may take longer; re-check after 60s.

## Files

- `cloudless.gr/scripts/generate_automation_checklist.py` — the generator
- `cloudless.gr/public/automation-checklist.pdf` — the output (committed)
- `cloudless.gr/src/app/links/page.tsx` — the links page referencing the PDF

## Trigger

- User says "update the checklist", "refresh the automation PDF",
  "monthly checklist update", or it's the 1st of the month.
- Can also be triggered by n8n workflow `monthly-checklist-reminder` (if wired).
