---
name: monthly-checklist-update
description: >-
  Update the 12-Automation Checklist lead-magnet PDF on cloudless.gr/links and announce it across all social channels. Run on the 1st of each month or when refreshing the checklist.
---

# Monthly Checklist PDF Update & Social Announce

Update the **12-Automation Checklist** lead-magnet PDF on `cloudless.gr/links`
and announce it across all social channels. Run on the **1st of each month**
(or when the user asks to refresh the checklist).

## Preflight

- The generator script and PDF live in the **separate `cloudless.gr` repo**,
  checked out at `~/cloudless.gr` (`Themis128/cloudless.gr`, branch `main`).
  Verify it exists and is clean first:
  ```bash
  git -C ~/cloudless.gr status --short --branch
  git -C ~/cloudless.gr fetch origin main
  ```
  If the checkout is missing: `git clone https://github.com/Themis128/cloudless.gr ~/cloudless.gr`.
- All `cloudless.gr/...` paths below resolve from `~/` (i.e. `~/cloudless.gr`),
  **not** from this repo. `social-api` must be running.

## What it does

1. **Review current services** — fetch `cloudless.gr/en/services` to confirm
   the service lineup and pricing haven't changed.
2. **Update the generator script** — edit
   `~/cloudless.gr/scripts/generate_automation_checklist.py`:
   - Refresh `CHECKLIST` items to reflect current production workflows.
   - Refresh `TOOLS` list if the stack changed.
   - Update the edition month/year string.
   - Update the CTA to match current services and pricing.
3. **Regenerate the PDF** — run inside `social-api` (has `reportlab`):
   ```bash
   docker cp ~/cloudless.gr/scripts/generate_automation_checklist.py \
     social-api:/app/scripts/generate_automation_checklist.py
   docker exec social-api python /app/scripts/generate_automation_checklist.py \
     /tmp/automation-checklist.pdf
   docker cp social-api:/tmp/automation-checklist.pdf \
     ~/cloudless.gr/public/automation-checklist.pdf
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
   | Threads          | `1071dcd5-1bc9-4770-923c-d897eb124485`   | Casual, direct — put `cloudless.gr/links` in the text |
   | Twitter/X        | `a89d6852-eff8-479a-835f-50d806cf59dd`   | Under 280 chars — **URL must be in the text body** (see Gotchas) |
   | Instagram        | `38ddbd44-8811-4d0b-be62-a23fd2f50490`   | **Requires image** — generate via `/api/v1/ai/generate-image` first; caption points to "link in bio" |
   | Facebook Page    | `ad83c946-0f6b-4fbf-bc63-543d2c2237f5`  | Business voice, `link_url` to `/links` |

6. **Archive to Nextcloud** — version the PDF on the OMV workspace
   (`/Marketing/checklist/`) via the `omv-nextcloud-ops` skill's
   `nextcloud-dav.py` tool:
   ```bash
   DAV=.devin/skills/omv-nextcloud-ops/nextcloud-integration/scripts/nextcloud-dav.py
   # dated archive copy (append-only, never overwritten)
   python3 $DAV upload ~/cloudless.gr/public/automation-checklist.pdf \
     "Marketing/checklist/automation-checklist-$(date +%Y-%m).pdf"
   # rolling 'latest' copy — overwriting preserves Nextcloud version history
   python3 $DAV upload ~/cloudless.gr/public/automation-checklist.pdf \
     Marketing/checklist/automation-checklist-latest.pdf
   ```
   `cloudless.gr/automation-checklist.pdf` stays the canonical public asset —
   the Nextcloud copy is the internal versioned archive for the team. Create a
   public share link (`nextcloud-dav.py share …`) only if a downloadable
   tracked mirror is explicitly wanted.

7. **Verify** — confirm all 6 targets reach `status=published` with URLs.
   Instagram permalink needs Graph API resolution (stored URL uses numeric ID).

## Gotchas

- **Instagram requires media** — text-only posts are `skipped`. Always attach
  an AI-generated image asset.
- **Twitter drops `link_url`** — `render_post_text` appends `link_url` only
  for LinkedIn and Facebook; the X publisher sends text only. The generated
  tweet **must contain `https://cloudless.gr/links` literally** — verify the
  rendered text includes it (and stays under 280 chars) before publishing.
- **Destination is `/links`, intentionally** — this campaign promotes the
  lead-magnet PDF, so all platforms point to `cloudless.gr/links` (which
  hosts the checklist download). This overrides the standard funnel CTAs
  (`/contact` for LinkedIn, `social.cloudless.gr/pricing` for X) for this
  campaign only.
- **Twitter char limit** — keep body under ~250 chars; hashtags and link add
  more. The 500 error from SocialAuto is a post-creation FK violation if the
  account ID is wrong — the correct Twitter ID is
  `a89d6852-eff8-479a-835f-50d806cf59dd`.
- **ruff format** — CI runs `ruff format --check`; always format the Python
  script before committing.
- **Facebook Page** posts go through Graph API (`pages_manage_posts` on the
  canonical `cloudless.gr` Page `ad83c946`). Personal-profile posts use the
  browser sidecar instead — don't target `9355ed63` here.

## Files

- `~/cloudless.gr/scripts/generate_automation_checklist.py` — the generator
- `~/cloudless.gr/public/automation-checklist.pdf` — the output (committed)
- `~/cloudless.gr/src/app/links/page.tsx` — the links page referencing the PDF

## Trigger

- User says "update the checklist", "refresh the automation PDF",
  "monthly checklist update", or it's the 1st of the month.
- Can also be triggered by n8n workflow `monthly-checklist-reminder` (if wired).
