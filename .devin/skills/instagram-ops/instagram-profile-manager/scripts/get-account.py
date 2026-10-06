#!/usr/bin/env python3
"""Get current authenticated account info.
Usage: get-account.py [session_id]"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import print_profile, sc  # noqa: E402

sid = sys.argv[1] if len(sys.argv) > 1 else ""
d = sc("GET", "/account", sid=sid)
print_profile(d if isinstance(d, dict) else {})
