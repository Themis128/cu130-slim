#!/usr/bin/env python3
"""Package the Meta support report template with screenshots into a zip.
Usage: package-report.py [output_zip]"""

import shutil
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import repo_root  # noqa: E402

ROOT = repo_root()
SCREENSHOT_DIR = ROOT / "meta-support-screenshots"
TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "template"
output = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "meta-support-report-package.zip"

print("=== Packaging Meta Support Report ===\n")

if not SCREENSHOT_DIR.is_dir():
    print(f"Error: Screenshot directory not found: {SCREENSHOT_DIR}")
    print("Run prepare-screenshots.py first.")
    sys.exit(1)
pngs = list(SCREENSHOT_DIR.glob("*.png"))
if not pngs:
    print(f"Error: No screenshots found in {SCREENSHOT_DIR}")
    sys.exit(1)

print(f"Screenshots: {len(pngs)} files")
print(f"Template: {TEMPLATE_DIR}")
print(f"Output: {output}\n")

README = """# Meta Support Report Package

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
"""

with tempfile.TemporaryDirectory() as tmp:
    pkg = Path(tmp) / "meta-support-report"
    (pkg / "screenshots").mkdir(parents=True)
    (pkg / "template").mkdir(parents=True)

    for f in pngs:
        shutil.copy(f, pkg / "screenshots")
    print(f"Copied {len(pngs)} screenshots")

    if TEMPLATE_DIR.is_dir():
        for f in TEMPLATE_DIR.iterdir():
            shutil.copy(f, pkg / "template")
    print("Copied template files")

    (pkg / "README.md").write_text(README)
    print("Created README.md")

    if shutil.which("zip"):
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as z:
            for f in pkg.rglob("*"):
                z.write(f, f.relative_to(tmp))
    else:
        output = output.with_suffix(".tar.gz")
        with tarfile.open(output, "w:gz") as t:
            t.add(pkg, arcname="meta-support-report")

size_mb = output.stat().st_size / (1024 * 1024)
print(f"\nPackage created: {output}")
print(f"Size: {size_mb:.1f}M" if size_mb >= 1 else f"Size: {output.stat().st_size / 1024:.0f}K")
