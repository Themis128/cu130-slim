#!/usr/bin/env python3
"""Save Instagram private API credentials.
Usage: set-instagram.py <username> <password>"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import set_secret, usage  # noqa: E402

if len(sys.argv) < 3:
    usage("set-instagram.py <username> <password>")
set_secret("INSTAGRAM_USERNAME", sys.argv[1], "Instagram private API username")
set_secret("INSTAGRAM_PASSWORD", sys.argv[2], "Instagram private API password")
