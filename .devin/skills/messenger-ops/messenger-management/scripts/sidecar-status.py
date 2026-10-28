#!/usr/bin/env python3
"""Check Messenger sidecar health + stats."""

import json
import subprocess
import sys
import urllib.error
import urllib.request


def show(title: str, url: str) -> None:
    print(title)
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            print(json.dumps(json.loads(r.read()), indent=2))
    except (urllib.error.URLError, ValueError) as e:
        print(f"(unavailable: {e})")
    print()


show("=== Sidecar Health ===", "http://localhost:9230/health")
show("=== Sidecar Stats ===", "http://localhost:9230/stats")
print("=== Sidecar Container Status ===")
subprocess.run(["docker", "ps", "--filter", "name=messenger-sidecar",
                "--format", "{{.Status}}"])
