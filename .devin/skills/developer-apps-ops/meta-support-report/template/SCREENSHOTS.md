# Meta Support Report — Screenshot Manifest

All screenshots are stored at:
`/home/tbaltzakis/cu130-slim/meta-support-screenshots/`

This directory is gitignored (see `.gitignore`).

## Screenshots for the Report a Problem submission

| # | Filename | Size | What it shows | Source URL |
|---|----------|------|---------------|------------|
| 01 | `01-restriction-dialog.png` | 240K | The "Your account is restricted right now" dialog blocking business verification | `https://developers.facebook.com/apps/1936126137016578/app-review/verification` |
| 02 | `02-submission-status.png` | 320K | App Review submission page with disabled Submit button | `https://developers.facebook.com/apps/1936126137016578/app-review/submissions/` |
| 03 | `03-alert-inbox.png` | 148K | Alert inbox showing the restriction notification | Facebook notifications |
| 04 | `04-ai-response.png` | 140K | Meta AI Business Assistant initial response | Business Support Home sidebar |
| 05 | `05-ai-full-response.png` | 142K | Meta AI Business Assistant full response | Business Support Home sidebar |
| 06 | `06-account-status.png` | 188K | Account Status page showing "Your account looks good" (contradicts restriction) | `https://www.facebook.com/account_status` |
| 07 | `07-ai-latest-response.png` | 144K | Meta AI latest response about the restriction | Business Support Home sidebar |
| 08 | `08-ai-final-response.png` | 144K | Meta AI final response confirming permanent ad limitation | Business Support Home sidebar |
| 09 | `09-ad-account-disabled.png` | 416K | Ad account disabled page showing "too much time has passed" | `https://www.facebook.com/business-support-home/1134463867/657781691826702/` |
| 10 | `10-report-problem-dialog.png` | 656K | Report a Problem dialog (initial) | Facebook home > Help & support > Report a problem |
| 11 | `11-after-include.png` | 656K | Report a Problem dialog after clicking "Include" | Same as above |
| 12 | `12-current-state.png` | 656K | Current state of the report form | Same as above |
| 13 | `13-report-form.png` | 836K | Report form with description and screenshots filled | Same as above |
| 14 | `14-screen-capture.png` | 707K | Screen capture attempt for the form | Same as above |

## Recommended screenshots for the report

Attach these 3 screenshots to the "Report a Problem" submission:

1. **`01-restriction-dialog.png`** — Shows the exact error blocking verification
2. **`09-ad-account-disabled.png`** — Shows the ad account is permanently disabled with no appeal
3. **`06-account-status.png`** — Shows the contradiction (Account Status says "looks good")

## How to attach screenshots

### Via the Report a Problem form
1. Click "Add a screenshot or video" in the report dialog
2. Select the screenshot files from `meta-support-screenshots/`
3. Or paste image files directly into the textarea

### Via Playwright MCP
```js
// Copy screenshots to the browser container first
docker compose cp meta-support-screenshots/01-restriction-dialog.png browser-novnc:/tmp/

// Then trigger the file input and upload
const fileInput = dialog.querySelector('input[type="file"]');
fileInput.click();
// Handle the file chooser with browser_file_upload
```

### Via the browser bridge (port 9223)
```bash
# Copy screenshots to the browser-novnc container
docker compose cp meta-support-screenshots/01-restriction-dialog.png browser-novnc:/tmp/

# Upload via the bridge API
curl -X POST http://localhost:9223/session/upload \
  -H "Content-Type: application/json" \
  -d '{"selector": "input[type=file]", "file_path": "/tmp/01-restriction-dialog.png"}'
```
