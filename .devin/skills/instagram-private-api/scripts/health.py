#!/usr/bin/env python3
"""Check aiograpi-rest sidecar health."""

import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import api_base  # noqa: E402

sidecar = api_base("INSTAGRAM_PRIVATE_API_URL", "http://localhost:8011")


def get(path: str) -> str:
    try:
        with urllib.request.urlopen(f"{sidecar}{path}", timeout=10) as r:
            return r.read().decode()
    except urllib.error.URLError:
        return "FAILED"


print(f"Checking sidecar at {sidecar}...")
resp = get("/health")
print(resp)
if '"ok"' in resp or '"status"' in resp:
    print("✓ Sidecar is healthy")
    print(f"Build: {get('/build')}")
else:
    print("✗ Sidecar is not responding")
    sys.exit(1)
