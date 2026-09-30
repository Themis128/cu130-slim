#!/usr/bin/env python3
"""Save TikTok private API key.
Usage: set-tiktok.py <api_key>"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import set_secret, usage  # noqa: E402

if len(sys.argv) < 2:
    usage("set-tiktok.py <api_key>")
set_secret("TIKTOK_PRIVATE_API_KEY", sys.argv[1], "TikTok private API signing key")
