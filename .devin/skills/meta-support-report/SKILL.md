# Meta Support Report (Report a Problem)

Submit a "Report a Problem" to Facebook through the Help & support menu. Use
when escalating account restrictions, business verification blocks, or App
Review issues that the Meta AI business assistant cannot resolve through
formal support channels.

## When to use this

- The Meta AI business assistant cannot create a formal support ticket
- The Account Quality "Request Review" button is not available (appeal window expired)
- Business verification is blocked by a personal account restriction
- You need to escalate to Facebook's human review team

## How to access

1. Go to `https://www.facebook.com/`
2. Click your **profile picture** (top right)
3. Click **Help & support**
4. Click **Report a problem**
5. Click **Include** (to include logs and diagnostics — recommended)
6. Fill in the report form (see below)
7. **Capture a screen area** (required — cannot be automated via Playwright)
8. Click **Submit report**

## Report form fields

### Description (required)

The description must be detailed and factual. Template:

```
I am unable to complete business verification for my Meta Developer app
(Cloudless, App ID: 1936126137016578). When I go to App Review > Verification
and click Start verification for my business portfolio cloudless.gr, I get
a dialog saying "Your account is restricted right now. You have been
temporarily blocked from performing this action."

My personal ad account (ID: 657781691826702) has been disabled since
Jan 24, 2021. Meta says "too much time has passed" and the decision cannot
be reviewed through Account Quality.

However, I need business verification to complete App Review for my
Cloudless app. I have completed all other App Review requirements (app
icon, privacy policy, permissions, screencast, data handling, reviewer
instructions). The only blocker is this verification step.

Please help lift this restriction so I can verify my business and submit
for review.
```

### Screenshots (recommended)

Attach screenshots that document the issue:
1. The restriction dialog ("Your account is restricted right now")
2. The ad account disabled page (showing "too much time has passed")
3. The App Review submission page (showing disabled Submit button)

Screenshots are stored in `meta-support-screenshots/` (gitignored).

### Screen capture (required)

The form requires selecting a screen area using the browser's native screen
capture API (`getDisplayMedia`). This **cannot be automated** via Playwright
because:
- It triggers a native browser dialog (not a DOM element)
- The user must manually select a screen or window region
- Playwright cannot interact with native OS-level dialogs

**Workaround**: Click "Capture Screen" and manually select the browser window
or a region showing the restriction. The captured area is attached to the
report automatically.

## Playwright automation notes

The "Report a Problem" form can be partially automated via Playwright MCP:

### What CAN be automated:
- Navigating to the form (profile > Help & support > Report a problem)
- Clicking "Include" to include logs
- Filling the description textarea (using native React value setter)
- Uploading screenshot files via the file input

### What CANNOT be automated:
- The "Capture Screen" step (requires native browser screen capture dialog)
- The final "Submit report" click (blocked until screen area is selected)

### Filling the textarea (React workaround)

Facebook uses React, so setting `textarea.value` directly doesn't trigger
React's state update. Use the native value setter:

```js
const textarea = dialog.querySelector('textarea');
const nativeSetter = Object.getOwnPropertyDescriptor(
  window.HTMLTextAreaElement.prototype, 'value'
).set;
nativeSetter.call(textarea, 'Your report text here...');
textarea.dispatchEvent(new Event('input', {bubbles: true}));
```

### Uploading screenshots

The file input is inside the dialog but hidden. Trigger it via DOM:

```js
const dialog = document.querySelectorAll('[role="dialog"]')[1];
const fileInput = dialog.querySelector('input[type="file"]');
fileInput.click();
// Then handle the file chooser with Playwright's browser_file_upload
```

### Dialog visibility workaround

The report dialog may be hidden (not visible to Playwright's accessibility
snapshot). Force it visible:

```js
const dialog = document.querySelectorAll('[role="dialog"]')[1];
dialog.style.display = 'block';
dialog.style.opacity = '1';
dialog.style.visibility = 'visible';
dialog.style.zIndex = '99999';
dialog.style.position = 'fixed';
dialog.style.background = 'white';
dialog.style.color = 'black';
```

## Alternative support channels

If "Report a Problem" doesn't work, try:

| Channel | URL | Notes |
|---------|-----|-------|
| Meta AI Business Assistant | Business Support Home sidebar | Chatbot, cannot create tickets |
| Facebook Help Center | `https://www.facebook.com/help/` | Search for specific issue |
| Developer Support | `https://developers.facebook.com/support/` | For developer-specific issues |
| Bug Reports | `https://developers.facebook.com/support/bugs/` | Platform bugs only |
| Report an incident | `https://developers.facebook.com/incident/report/` | Active incidents |
| Business Help Center | `https://www.facebook.com/business/help/` | Business-specific help |

## Template package

The `template/` directory contains ready-to-use report materials:

| File | Purpose |
|------|---------|
| `template/report-description.txt` | The full report description text (paste into the form) |
| `template/SCREENSHOTS.md` | Manifest of all 14 screenshots with descriptions and source URLs |

Screenshots are stored at `/home/tbaltzakis/cu130-slim/meta-support-screenshots/`
(gitignored). Run `scripts/package-report.sh` to bundle everything into a
portable `.tar.gz` archive.

## Scripts

- `scripts/print-report-template.sh` — Print the report description template
- `scripts/list-screenshots.sh` — List available screenshots for attachment
- `scripts/prepare-screenshots.sh` — Capture screenshots via Playwright MCP
- `scripts/package-report.sh` — Bundle screenshots + template into a .tar.gz

## Related skills

- `meta-account-restriction` — Diagnose and resolve account restrictions
- `meta-app-review` — App Review submission and permission management
- `browser-bridge-ops` — Browser automation for Meta pages
