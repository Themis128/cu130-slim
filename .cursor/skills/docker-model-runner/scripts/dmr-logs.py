#!/usr/bin/env python3
"""Fetch DMR logs.
Usage: dmr-logs.py [--no-engines] [lines]"""
import subprocess
import sys

no_engines, lines = [], 50
for a in sys.argv[1:]:
    if a == "--no-engines":
        no_engines.append("--no-engines")
    else:
        lines = int(a)

print(f"=== DMR Logs (last {lines} lines) ===\n")
r = subprocess.run(["docker", "model", "logs", *no_engines],
                   capture_output=True, text=True)
print("\n".join((r.stdout + r.stderr).splitlines()[-lines:]))
