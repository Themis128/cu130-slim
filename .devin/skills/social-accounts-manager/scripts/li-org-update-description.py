#!/usr/bin/env python3
"""Update LinkedIn Organization description (2000 char limit) via browser sidecar.
Usage: li-org-update-description.py "Description text" OR li-org-update-description.py /path/to/file.txt"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _li_org_update import update_field  # noqa: E402
from skill_http import usage  # noqa: E402

val = sys.argv[1] if len(sys.argv) > 1 else usage('li-org-update-description.py "Description text"')
if Path(val).is_file():
    val = Path(val).read_text()
update_field("textarea#organization-description-field", "HTMLTextAreaElement", val, "description")
