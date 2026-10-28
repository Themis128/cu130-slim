#!/usr/bin/env python3
"""Push a local DMR model to Docker Hub or HuggingFace.
Usage: dmr-push.py <model>"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import docker_model  # noqa: E402
from skill_http import usage  # noqa: E402

model = sys.argv[1] if len(sys.argv) > 1 else usage("dmr-push.py <model>")
print(f"Pushing model: {model}")
print("This may take a while for large models...\n")
docker_model("push", model)
print("\n=== Push complete ===")
