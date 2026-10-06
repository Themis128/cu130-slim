#!/usr/bin/env python3
"""Sync brand to ALL connected profiles (bio + name + picture where supported).
Usage: sync-all.py"""

import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent

print("=== Brand Sync: All Platforms ===\n")

print("--- Current Status ---")
subprocess.run([sys.executable, str(SCRIPT_DIR / "sync-status.py")])
print()

print("--- Syncing Bios ---")
subprocess.run([sys.executable, str(SCRIPT_DIR / "sync-bio.py")])
print()

print("--- Syncing Profile Pictures ---")
subprocess.run([sys.executable, str(SCRIPT_DIR / "sync-profile-pic.py")])
print()

print("--- Final Status ---")
subprocess.run([sys.executable, str(SCRIPT_DIR / "sync-status.py")])

print("\n=== Brand sync complete ===")
