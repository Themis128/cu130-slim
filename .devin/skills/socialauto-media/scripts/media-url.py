#!/usr/bin/env python3
"""Print the view URL for a media asset by storage path.
Usage: media-url.py <storage-path>"""

import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import api_base, usage  # noqa: E402

path_val = sys.argv[1] if len(sys.argv) > 1 else usage("media-url.py <storage-path>")
api = api_base("SOCIAL_API_URL", "http://127.0.0.1:8083")
print(f"{api}/api/v1/media/view?path={urllib.parse.quote(path_val)}")
