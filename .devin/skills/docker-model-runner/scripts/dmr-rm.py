#!/usr/bin/env python3
"""Remove a local DMR model.
Usage: dmr-rm.py <model> [--force]"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import docker_model  # noqa: E402
from skill_http import usage  # noqa: E402

if len(sys.argv) < 2:
    usage("dmr-rm.py <model> [--force]")
model, rest = sys.argv[1], sys.argv[2:]
force = ["-f"] if rest and rest[0] in ("--force", "-f") else []
print(f"Removing model: {model}{' (force)' if force else ''}")
docker_model("rm", *force, model)
print("Done.")
