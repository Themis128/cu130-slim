#!/usr/bin/env python3
"""Check if a Meta access token is valid and show its expiry.
Usage: check-meta-token.py <access_token> [graph|threads]"""

import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import usage  # noqa: E402

token = sys.argv[1] if len(sys.argv) > 1 else usage("check-meta-token.py <token> [graph|threads]")
host = sys.argv[2] if len(sys.argv) > 2 else "graph"
api = "https://graph.threads.net" if host == "threads" else "https://graph.facebook.com"

print(f"Checking token at {api}...\n")
req = urllib.request.Request(
    f"{api}/debug_token?input_token={token}&access_token={token}")
try:
    with urllib.request.urlopen(req, timeout=15) as r:
        print(json.dumps(json.loads(r.read().decode()), indent=2))
except Exception as e:
    print(e)
