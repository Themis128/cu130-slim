#!/usr/bin/env python3
"""Add professional links to ALL connected social accounts.
Website: https://cloudless.gr
Portfolio: https://baltzakisthemis.com
WhatsApp: https://wa.me/306977777838"""

import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent

STEPS = [
    ("Facebook Page (cloudless.gr)", "fb-page-update-website.py", "https://cloudless.gr"),
    ("LinkedIn Organization (cloudless.gr)", "li-org-update-website.py", "https://cloudless.gr"),
    ("LinkedIn Personal", "li-personal-update-website.py", "https://cloudless.gr"),
    ("Facebook Personal", "fb-personal-update-website.py", "https://cloudless.gr"),
    ("Instagram", "ig-update-url.py", "https://cloudless.gr"),
]

print("==========================================")
print("  Adding professional links to all accounts")
print("==========================================")

for label, script, arg in STEPS:
    print(f"\n--- {label} ---")
    r = subprocess.run([sys.executable, str(SKILL_DIR / script), arg])
    if r.returncode != 0:
        print("  (failed)")

print("\n==========================================")
print("  Done. Run read-all.py to verify.")
print("==========================================")
