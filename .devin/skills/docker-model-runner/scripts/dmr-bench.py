#!/usr/bin/env python3
"""Benchmark a DMR model's performance.
Usage: dmr-bench.py <model> [concurrency] [duration]"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import usage  # noqa: E402

if len(sys.argv) < 2:
    usage("dmr-bench.py <model> [concurrency] [duration]")
model = sys.argv[1]
concurrency = sys.argv[2] if len(sys.argv) > 2 else "1,2,4,8"
duration = sys.argv[3] if len(sys.argv) > 3 else "30s"

print(f"Benchmarking: {model}")
print(f"Concurrency: {concurrency}")
print(f"Duration: {duration}\n")
r = subprocess.run(["docker", "model", "bench", "--json",
                    "--concurrency", *concurrency.split(","),
                    "--duration", duration, model],
                   capture_output=True, text=True)
try:
    print(json.dumps(json.loads(r.stdout), indent=2))
except Exception:
    print(r.stdout + r.stderr)
