#!/usr/bin/env python3
"""List available screenshots in meta-support-screenshots/ for report
attachment."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import repo_root  # noqa: E402

SCREENSHOT_DIR = repo_root() / "meta-support-screenshots"

if not SCREENSHOT_DIR.is_dir():
    print(f"No screenshots directory found at {SCREENSHOT_DIR}")
    print("Run prepare-screenshots.py first to capture screenshots.")
    sys.exit(0)

print(f"Available screenshots in {SCREENSHOT_DIR}:\n")
pngs = sorted(SCREENSHOT_DIR.glob("*.png"))
for f in pngs:
    size = f.stat().st_size
    human = f"{size / 1024:.0f}K" if size < 1024 * 1024 else f"{size / 1048576:.1f}M"
    print(f"  {f.name} ({human})")

print(f"\nTotal: {len(pngs)} screenshots\n")
print("Recommended for report submission:")
print("  01-restriction-dialog.png — The restriction dialog")
print("  09-ad-account-disabled.png — Ad account disabled page\n")
print("To attach: Use the 'Add a screenshot or video' button in the")
print("Report a Problem dialog, or paste image files directly.")
