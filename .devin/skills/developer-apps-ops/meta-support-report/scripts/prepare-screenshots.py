#!/usr/bin/env python3
"""Prepare screenshots for a Meta support report by listing what to
capture. This script guides the user through capturing the required
screenshots."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import repo_root  # noqa: E402

SCREENSHOT_DIR = repo_root() / "meta-support-screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

print("=== Meta Support Report Screenshot Preparation ===\n")
print("This script lists the screenshots that should be captured for a")
print("Meta 'Report a Problem' submission about account restrictions")
print("blocking business verification.\n")
print(f"Screenshot directory: {SCREENSHOT_DIR}\n")

existing = len(list(SCREENSHOT_DIR.glob("*.png")))
print(f"Existing screenshots: {existing}\n")

SHOTS = [
    ("01-restriction-dialog.png",
     "https://developers.facebook.com/apps/1936126137016578/app-review/verification",
     "The dialog saying 'Your account is restricted right now'"),
    ("09-ad-account-disabled.png",
     "https://www.facebook.com/business-support-home/1134463867/657781691826702/",
     "The page showing 'Disabled' status and 'too much time has passed'"),
    ("02-submission-status.png",
     "https://developers.facebook.com/apps/1936126137016578/app-review/submissions/",
     "The App Review submission status"),
]

print("=== Required screenshots ===\n")
for i, (name, url, capture) in enumerate(SHOTS, 1):
    print(f"{i}. {name}")
    print(f"   URL: {url}")
    print(f"   Capture: {capture}\n")
