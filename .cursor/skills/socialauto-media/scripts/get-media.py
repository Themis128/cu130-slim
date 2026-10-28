#!/usr/bin/env python3
"""Get media asset details.
Usage: get-media.py <media-id>"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import api_base, api_login, request, usage  # noqa: E402

api = api_base("SOCIAL_API_URL", "http://127.0.0.1:8083")
id = sys.argv[1] if len(sys.argv) > 1 else usage("get-media.py <media-id>")
TOKEN = api_login(api)
resp = request("GET", f"{api}/api/v1/media/{id}", token=TOKEN)
print(json.dumps(resp, indent=2, ensure_ascii=False))
