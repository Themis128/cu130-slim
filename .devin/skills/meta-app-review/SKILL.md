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

## Permissions in the submission

### Completed (allowed-usage saved)

| Permission | Status |
|------------|--------|
| `pages_show_list` | Done |
| `pages_manage_metadata` | Done |
| `pages_messaging` | Done |
| `business_management` | Done |
| `pages_read_engagement` | Done |
| `instagram_business_basic` | Done |
| `instagram_business_manage_messages` | Done |
| `pages_read_user_content` | Done |
| `pages_manage_posts` | Done |
| `pages_manage_engagement` | Done |
| `pages_utility_messaging` | Done (screencast uploaded) |

### Remaining (allowed-usage not yet saved)

| Permission | Notes |
|------------|-------|
| `instagram_business_content_publish` | Needs description + screencast |
| `instagram_manage_comments` | Needs description + screencast |
| `instagram_business_manage_insights` | Needs description + screencast |
| `threads_basic` | Needs description + screencast |
| `read_insights` | Needs description + screencast |

## App Review requirements per permission

Each permission requires ALL of the following before the submission button enables:

1. **Allowed-usage description**: A detailed explanation of how the permission is
   used in the app. Must be specific — generic descriptions are rejected.
2. **Screencast video**: A real MP4 (H.264, 1280×720 minimum) showing the
   end-to-end user experience including OAuth flow. Screenshot-based videos may
   be rejected.
3. **API test calls**: Some permissions require test calls in Meta's Testing
   area. Meta may take up to 24 hours to process.
4. **Compliance checkbox**: Agree to comply with the allowed usage terms.
5. **Customized questions**: Some permissions have tech-provider-specific
   questions that must be answered.
6. **Dependent permissions**: Ensure all dependent permissions are included
   (e.g., `instagram_business_basic` for Instagram publishing).

## Current blocker

**Business verification is blocked** by a permanent advertising restriction on
the personal Facebook account (Themistoklis Baltzakis, ad account
`657781691826702`, disabled Jan 24, 2021). Meta says "too much time has passed"
and the decision cannot be reviewed through Account Quality.

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
