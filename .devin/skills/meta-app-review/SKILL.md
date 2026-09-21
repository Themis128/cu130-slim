# Meta App Review Management

Manage the Meta App Review submission for the Cloudless app, including business
verification, permission allowed-usage, screencasts, reviewer instructions, and
final submission. Use when checking App Review status, completing permission
requirements, uploading screencasts, or debugging the "Submit for review" button
being disabled.

## App identifiers

- **App ID**: `1936126137016578`
- **App name**: Cloudless
- **App Review submission ID**: `2047300442565813`
- **Business portfolio**: `cloudless.gr` (ID: `1558125105019725`)
- **Privacy policy**: `https://social.cloudless.gr/privacy`
- **Data deletion**: `https://social.cloudless.gr/data-deletion`
- **Webhook URL**: `https://social.cloudless.gr/api/v1/messenger/webhook`
- **Webhook verify token**: `cloudless_messenger_verify`

## Key URLs

| Resource | URL |
|----------|-----|
| App Dashboard | `https://developers.facebook.com/apps/1936126137016578` |
| App Review submission | `https://developers.facebook.com/apps/1936126137016578/app-review/submissions/?submission_id=2047300442565813` |
| Business verification | `https://developers.facebook.com/apps/1936126137016578/app-review/verification` |
| Business Support Home | `https://www.facebook.com/business-support-home/` |
| Account Status | `https://www.facebook.com/account_status` |
| Account Quality | `https://www.facebook.com/accountquality` (redirects to BSH if restricted) |

## Submission state (2026-09-21)

The draft submission was pruned to only permissions SocialAuto actually uses —
Meta rejects submissions containing undemonstrable permissions. All remaining
items show **Edit** (allowed-usage saved); nothing is incomplete.

### Removed from the draft (with evidence)

| Item | Why removed |
|------|-------------|
| `whatsapp_business_messaging` + `_management` | Can't attach to the app — WhatsApp onboarding is Meta-blocked; can't demo. Re-add via WhatsApp use case when onboarding unblocks. |
| `pages_utility_messaging` | 0 lifetime API calls; no code refs (utility message templates only — messenger bot uses `pages_messaging`). |
| `instagram_business_manage_insights` | 0 lifetime calls; analytics uses `instagram_manage_insights` (FB-login family) instead. |
| `instagram_manage_comments` | 0 calls AND can't run — cloudless.gr IG uses Instagram Business Login and is not linked to a Page. The working `instagram_business_manage_comments` stays. |
| `Human Agent` feature | 0 calls; no `human_agent` tag in messenger code. |

### How to remove a permission

Two levels: (a) use-case level — `Use cases → Customize → Permissions and features`
tab → row `Actions → Remove` (removes from app + propagates to the draft);
(b) submission level — `App Review → submissions` list page, per-item `remove`
button (for items whose permission isn't attached to any use case, e.g. the
WhatsApp zombies). Confirm dialog: "Yes, remove".

### Remaining permissions (all allowed-usage saved)

pages_show_list, pages_manage_metadata, pages_messaging, business_management,
pages_read_engagement, instagram_business_basic, instagram_business_manage_messages,
pages_read_user_content, pages_manage_posts, instagram_business_content_publish,
pages_manage_engagement, threads_basic, instagram_content_publish,
instagram_manage_messages, instagram_business_manage_comments, read_insights,
ads_read, ads_management, public_profile, instagram_manage_insights, instagram_basic

## Wizard steps status

| Step | Status |
|------|--------|
| Verification | **BLOCKED** — connecting `cloudless.gr` portfolio returns "temporarily blocked from performing this action" (personal-account restriction) |
| App settings | Done — icon, privacy URL `social.cloudless.gr/privacy` (public 200), category "Business and pages", contact email |
| Allowed usage | Done — all items saved |
| Data handling | Done — pre-filled reviewed (Cloudflare processor, controller Baltzakis Themistoklis, Greece) |
| Reviewer instructions | Done — includes test account creds |

## Reviewer test account

- `reviewer@cloudless.gr` — EDITOR role on the admin (enterprise) team.
- Created directly in DB (no own team → team resolution lands on admin team).
- Credentials are in the submission's access-code field AND the instructions text.
- **Delete this account after review concludes.**

## Cloudflare Access exposure (pending decision implementation)

`social.cloudless.gr` root is behind Access SSO (302 → cloudflareaccess.com) —
same wall that got TikTok rejected. Chosen fix: **temporary full Access bypass**
on the hostname during the review window (app's own auth still gates everything;
exposure = login page only). Apply via `cloudflare-access-paths` skill right
before submitting, revert after approval. Not applied yet — no benefit while
Verification is blocked.

## API-call usage table

Each use-case permissions tab shows real per-permission API call counts — use it
to prove usage before deciding keep/remove: 0 calls = removal candidate.
Observed: ads_management 2.6k, business_management 4k, pages_messaging 8.8k,
pages_manage_metadata 9.4k, pages_read_engagement 9.4k, instagram_basic 2.4k,
public_profile 2.5k, instagram_business_manage_messages 366,
instagram_content_publish 360, instagram_manage_messages 8.

## Current blocker

**Business verification is blocked** by a permanent advertising restriction on
the personal Facebook account (Themistoklis Baltzakis, ad account
`657781691826702`, disabled Jan 24, 2021). Meta says "too much time has passed"
and the decision cannot be reviewed through Account Quality. The same
restriction blocks: business-portfolio connect ("temporarily blocked"),
WhatsApp onboarding ("temporarily blocked").

See the `meta-account-restriction` skill for resolution steps.

## Screencast generation

The screencast was generated from SocialAuto screenshots using ffmpeg inside
the `social-api` container:

```bash
# Generate screencast from screenshots (inside social-api container)
docker compose exec -T social-api bash -c '
  mkdir -p /tmp/screencast-output
  ffmpeg -y -framerate 5 -i /tmp/screencast-frames/frame_%02d.png \
    -c:v libx264 -preset slow -crf 20 -pix_fmt yuv420p \
    -s 1280x720 /tmp/screencast-output/cloudless-screencast.mp4
'
# Copy back to host
docker compose cp social-api:/tmp/screencast-output/cloudless-screencast.mp4 \
  ./cloudless-screencast.mp4
```

The screencast is ~45 seconds, 1280×720, H.264/MP4, ~365 KB. It contains:
landing page, login, dashboard, messenger UI, bot health, accounts, privacy
policy, webhook response, data deletion policy.

## File upload workaround

Meta's file input is intercepted by an overlay div (`<div class="_3ixn"></div>`).
Use DOM evaluation to trigger the file input directly:

```js
// Find the file input by ID (changes each render — inspect the DOM)
const input = document.getElementById('js_96');
if (input) input.click();
```

Then handle the file chooser:
```js
await fileChooser.setFiles(["/workspace/cloudless-screencast.mp4"]);
```

## Checkbox click workaround

Meta's compliance checkbox is intercepted by a modal overlay. Click directly:

```js
const checkboxes = document.querySelectorAll('input[type="checkbox"]');
for (const cb of checkboxes) {
  if (cb.closest('[role="dialog"]')) { cb.click(); break; }
}
```

## Save button workaround

The Save button inside the allowed-usage dialog may not be found by role.
Use DOM evaluation:

```js
const buttons = document.querySelectorAll('[role="button"]');
for (const btn of buttons) {
  if (btn.textContent.trim() === 'Save' && btn.closest('[role="dialog"]')) {
    btn.click(); break;
  }
}
```

## Scripts

- `scripts/check-review-status.sh` — Check App Review submission status via Graph API
- `scripts/check-business-verification.sh` — Check business verification status
- `scripts/list-permissions.sh` — List all permissions and their review status
- `scripts/generate-screencast.sh` — Generate a screencast from screenshots using ffmpeg

## Related skills

- `meta-account-restriction` — Resolve personal account restrictions blocking verification
- `meta-support-report` — Submit a "Report a Problem" to Facebook support
- `meta-oauth-setup` — OAuth configuration for Facebook/Instagram/Threads
- `messenger-platform` — Messenger webhook and bot configuration
