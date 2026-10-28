#!/usr/bin/env python3
"""Upload a file to an input[type=file] element in the browser.
Usage: bridge-upload.py <file_path> <selector> [click_selector]"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import api_base, request, usage  # noqa: E402

bridge = api_base("BROWSER_BRIDGE_URL", "http://localhost:9223")
if len(sys.argv) < 3:
    usage("bridge-upload.py <file_path> <selector> [click_selector]")
body = {"selector": sys.argv[2], "file_path": sys.argv[1]}
if len(sys.argv) > 3:
    body["click_selector"] = sys.argv[3]
d = request("POST", f"{bridge}/session/upload", data=body)
print(f'Status: {d.get("status", "?")}')
print(f'Uploaded: {d.get("uploaded", False)}')
print(f'Method: {d.get("method", "?")}')
