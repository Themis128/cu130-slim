#!/usr/bin/env python3
"""Check if the instagrapi sidecar is healthy and running."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import sidecar_get, sidecar_healthy  # noqa: E402

print("=== Checking instagrapi sidecar ===", file=sys.stderr)
resp = sidecar_get("/health")
if not resp:
    print("❌ Sidecar not responding at http://localhost:8011", file=sys.stderr)
    sys.exit(1)
if sidecar_healthy():
    print("✅ Sidecar healthy", file=sys.stderr)
else:
    print(f"⚠️ Sidecar responded but unhealthy: {resp}", file=sys.stderr)
    sys.exit(1)
