#!/usr/bin/env python3
"""Pull a new model to DMR.
Usage: dmr-pull.py <model-name>"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import docker_model  # noqa: E402
from skill_http import usage  # noqa: E402

model = sys.argv[1] if len(sys.argv) > 1 else usage("dmr-pull.py <model-name>")
print(f"Pulling model: {model}")
print("This may take a while for large models...\n")
docker_model("pull", model)

print("\n=== Model pulled successfully ===")
r = subprocess.run(["docker", "model", "inspect", model],
                   capture_output=True, text=True)
try:
    d = json.loads(r.stdout)
    print(f'  Tags: {", ".join(d.get("tags", []))}')
    print(f'  Format: {d.get("config", {}).get("format", "?")}')
    print(f'  ID: {d.get("id", "?")[:20]}')
except Exception:
    pass
