#!/usr/bin/env python3
"""Update Facebook Page long description (Graph API).
Usage: fb-page-update-description.py "Long description text" OR file path"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fb_page_update import update_field  # noqa: E402
from skill_http import usage  # noqa: E402

val = sys.argv[1] if len(sys.argv) > 1 else usage('fb-page-update-description.py "Long description text"')
if Path(val).is_file():
    val = Path(val).read_text()
update_field("description", val)
