#!/usr/bin/env python3
"""Run all bot-guardrail test scripts.
Usage: test-all.py"""

import subprocess
import sys
from pathlib import Path

here = Path(__file__).resolve().parent
tests = sorted(p for p in here.glob("test-*.py") if p.name != "test-all.py")
print(f"=== Running {len(tests)} guardrail tests ===\n")
failed = []
for t in tests:
    print(f"--- {t.name} ---")
    rc = subprocess.run([sys.executable, str(t)]).returncode
    if rc:
        failed.append(t.name)
print()
if failed:
    print(f"FAILED: {failed}")
    sys.exit(1)
print("All guardrail tests passed.")
