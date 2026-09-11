#!/usr/bin/env bash
# Package the Meta support report template with screenshots into a zip.
# Usage: bash package-report.sh [output_zip]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
SCREENSHOT_DIR="$REPO_ROOT/meta-support-screenshots"
TEMPLATE_DIR="$SCRIPT_DIR/../template"
OUTPUT="${1:-$REPO_ROOT/meta-support-report-package.zip}"

echo "=== Packaging Meta Support Report ==="
echo ""

# Check screenshots exist
if [ ! -d "$SCREENSHOT_DIR" ]; then
  echo "Error: Screenshot directory not found: $SCREENSHOT_DIR"
  echo "Run prepare-screenshots.sh first to capture screenshots."
  exit 1
fi

SCREENSHOT_COUNT=$(ls "$SCREENSHOT_DIR"/*.png 2>/dev/null | wc -l || echo "0")
if [ "$SCREENSHOT_COUNT" -eq 0 ]; then
  echo "Error: No screenshots found in $SCREENSHOT_DIR"
  exit 1
fi

echo "Screenshots: $SCREENSHOT_COUNT files"
echo "Template: $TEMPLATE_DIR"
echo "Output: $OUTPUT"
echo ""

# Create a temp directory for packaging
TMP_DIR=$(mktemp -d)
mkdir -p "$TMP_DIR/meta-support-report/screenshots"
mkdir -p "$TMP_DIR/meta-support-report/template"

# Copy screenshots
cp "$SCREENSHOT_DIR"/*.png "$TMP_DIR/meta-support-report/screenshots/"
echo "Copied $SCREENSHOT_COUNT screenshots"

# Copy template files
cp "$TEMPLATE_DIR"/* "$TMP_DIR/meta-support-report/template/" 2>/dev/null || true
echo "Copied template files"

# Create a README
cat > "$TMP_DIR/meta-support-report/README.md" <<'README'
# Meta Support Report Package

This package contains everything needed to submit a "Report a Problem" to
Facebook about the account restriction blocking business verification.

## Contents

- `template/report-description.txt` — The report description text (paste into the form)
- `template/SCREENSHOTS.md` — Manifest of all screenshots with descriptions
- `screenshots/` — All captured screenshots (14 PNG files)

## How to submit

1. Go to https://www.facebook.com/
2. Click profile picture (top right) > Help & support > Report a problem
3. Click "Include" (to include logs and diagnostics)
4. Paste the contents of `template/report-description.txt` into the textarea
5. Click "Capture Screen" and select a screen area (required — native browser dialog)
6. Attach screenshots from the `screenshots/` folder (recommended: 01, 06, 09)
7. Click "Submit report"

## Key identifiers

- App ID: 1936126137016578
- App name: Cloudless
- Business portfolio: cloudless.gr (ID: 1558125105019725)
- Ad account: 657781691826702 (disabled Jan 24, 2021)
README

echo "Created README.md"

# Create the archive (tar.gz — zip may not be installed)
cd "$TMP_DIR"
if command -v zip > /dev/null 2>&1; then
  zip -r "$OUTPUT" meta-support-report/ > /dev/null
else
  OUTPUT="${OUTPUT%.zip}.tar.gz"
  tar czf "$OUTPUT" meta-support-report/
fi
echo ""
echo "Package created: $OUTPUT"
echo "Size: $(du -h "$OUTPUT" | cut -f1)"

# Cleanup
rm -rf "$TMP_DIR"
