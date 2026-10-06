#!/usr/bin/env python3
"""Save LinkedIn browser automation credentials.
Usage: set-linkedin.py <username> <password>"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import set_secret, usage  # noqa: E402

if len(sys.argv) < 3:
    usage("set-linkedin.py <username> <password>")
set_secret("LINKEDIN_USERNAME", sys.argv[1], "LinkedIn browser automation username")
set_secret("LINKEDIN_PASSWORD", sys.argv[2], "LinkedIn browser automation password")
