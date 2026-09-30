#!/usr/bin/env python3
"""Update LinkedIn Organization tagline via browser sidecar.
Usage: li-org-update-tagline.py "Tagline text" """

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _li_org_update import update_field  # noqa: E402
from skill_http import usage  # noqa: E402

val = sys.argv[1] if len(sys.argv) > 1 else usage('li-org-update-tagline.py "Tagline text"')
update_field("textarea#organization-tagline-field", "HTMLTextAreaElement", val, "tagline")
