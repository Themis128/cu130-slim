#!/usr/bin/env bash
# Prepare screenshots for a Meta support report by capturing key pages.
# This script guides the user through capturing the required screenshots.
# Usage: bash prepare-screenshots.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
SCREENSHOT_DIR="$REPO_ROOT/meta-support-screenshots"

mkdir -p "$SCREENSHOT_DIR"

echo "=== Meta Support Report Screenshot Preparation ==="
echo ""
echo "This script lists the screenshots that should be captured for a"
echo "Meta 'Report a Problem' submission about account restrictions"
echo "blocking business verification."
echo ""
echo "Screenshot directory: $SCREENSHOT_DIR"
echo ""

# Check for existing screenshots
EXISTING=$(ls "$SCREENSHOT_DIR"/*.png 2>/dev/null | wc -l || echo "0")
echo "Existing screenshots: $EXISTING"
echo ""

echo "=== Required screenshots ==="
echo ""
echo "1. Restriction dialog (01-restriction-dialog.png)"
echo "   URL: https://developers.facebook.com/apps/1936126137016578/app-review/verification"
echo "   Capture: The dialog saying 'Your account is restricted right now'"
echo ""
echo "2. Ad account disabled (09-ad-account-disabled.png)"
echo "   URL: https://www.facebook.com/business-support-home/1134463867/657781691826702/"
echo "   Capture: The page showing 'Disabled' status and 'too much time has passed'"
echo ""
echo "3. App Review submission status (02-submission-status.png)"
echo "   URL: https://developers.facebook.com/apps/1936126137016578/app-review/submissions/"
echo "   Capture: The submission page with disabled Submit button"
echo ""
echo "4. Account Status (06-account-status.png)"
echo "   URL: https://www.facebook.com/account_status"
echo "   Capture: The page showing account status (may show 'looks good')"
echo ""
echo "=== How to capture ==="
echo ""
echo "Option A: Use Playwright MCP browser_take_screenshot"
echo "  - Navigate to the URL"
echo "  - Take a screenshot with filename parameter"
echo ""
echo "Option B: Manual capture"
echo "  - Open each URL in your browser"
echo "  - Take a screenshot (PrtScn or Cmd+Shift+4)"
echo "  - Save as PNG to $SCREENSHOT_DIR"
echo ""
echo "=== After capturing ==="
echo ""
echo "Run list-screenshots.sh to verify all screenshots are present."
echo "Then use the Report a Problem flow to submit with the screenshots attached."
