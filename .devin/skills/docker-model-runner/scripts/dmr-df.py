#!/usr/bin/env python3
"""Show DMR disk usage."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import dmr_get, docker_model  # noqa: E402

print("=== Docker Model Runner Disk Usage ===")
raw = dmr_get("/inference/df")
if raw is not None:
    print(json.dumps(json.loads(raw), indent=2))
elif docker_model("df") != 0:
    print("  (DMR offline or df failed)")
