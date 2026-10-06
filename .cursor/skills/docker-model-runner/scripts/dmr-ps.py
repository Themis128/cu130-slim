#!/usr/bin/env python3
"""List running (loaded in memory) DMR models."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import dmr_get, docker_model  # noqa: E402

print("=== Running Models (loaded in memory) ===")
raw = dmr_get("/inference/ps")
if raw is not None:
    d = json.loads(raw)
    items = d if isinstance(d, list) else [d]
    if not items:
        print("  (no models loaded)")
    for item in items:
        status = ("IN USE" if item.get("in_use")
                  else "LOADING" if item.get("loading") else "IDLE")
        print(f'  {item.get("model_name", "?")} [{item.get("backend_name", "?")}] '
              f'mode={item.get("mode", "?")} status={status} '
              f'last_used={item.get("last_used", "?")}')
elif docker_model("ps") != 0:
    print("  (no models loaded or DMR offline)")
