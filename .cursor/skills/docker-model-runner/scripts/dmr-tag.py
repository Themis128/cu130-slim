#!/usr/bin/env python3
"""Tag a local DMR model with a new name.
Usage: dmr-tag.py <source> <target>"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import docker_model  # noqa: E402
from skill_http import usage  # noqa: E402

if len(sys.argv) < 3:
    usage("dmr-tag.py <source> <target>")
print(f"Tagging: {sys.argv[1]} → {sys.argv[2]}")
docker_model("tag", sys.argv[1], sys.argv[2])
print("Done.")
