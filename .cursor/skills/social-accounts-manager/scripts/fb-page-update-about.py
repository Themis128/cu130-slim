#!/usr/bin/env python3
"""Update Facebook Page about (100 char limit).
Usage: fb-page-update-about.py "About text" """

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fb_page_update import update_field  # noqa: E402
from skill_http import usage  # noqa: E402

val = sys.argv[1] if len(sys.argv) > 1 else usage('fb-page-update-about.py "About text"')
update_field("about", val, max_len=100)
