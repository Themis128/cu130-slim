#!/usr/bin/env bash
# List available screenshots in meta-support-screenshots/ for report attachment.
# Usage: bash list-screenshots.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
SCREENSHOT_DIR="$REPO_ROOT/meta-support-screenshots"

if [ ! -d "$SCREENSHOT_DIR" ]; then
  echo "No screenshots directory found at $SCREENSHOT_DIR"
  echo "Run prepare-screenshots.sh first to capture screenshots."
  exit 0
fi

echo "Available screenshots in $SCREENSHOT_DIR:"
echo ""

COUNT=0
for f in "$SCREENSHOT_DIR"/*.png; do
  [ -f "$f" ] || continue
  SIZE=$(du -h "$f" | cut -f1)
  NAME=$(basename "$f")
  echo "  $NAME ($SIZE)"
  COUNT=$((COUNT + 1))
done

echo ""
echo "Total: $COUNT screenshots"
echo ""
echo "Recommended for report submission:"
echo "  01-restriction-dialog.png — The restriction dialog"
echo "  09-ad-account-disabled.png — Ad account disabled page"
echo ""
echo "To attach: Use the 'Add a screenshot or video' button in the"
echo "Report a Problem dialog, or paste image files directly."
