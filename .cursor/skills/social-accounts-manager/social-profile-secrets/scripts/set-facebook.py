#!/usr/bin/env python3
"""Save Facebook browser automation credentials.
Usage: set-facebook.py <username> <password>"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import set_secret, usage  # noqa: E402

if len(sys.argv) < 3:
    usage("set-facebook.py <username> <password>")
set_secret("FACEBOOK_USERNAME", sys.argv[1], "Facebook browser automation username")
set_secret("FACEBOOK_PASSWORD", sys.argv[2], "Facebook browser automation password")
