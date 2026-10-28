#!/usr/bin/env python3
"""Update LinkedIn personal headline via browser sidecar.
Usage: li-personal-update-headline.py "Headline text"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import http, print_result  # noqa: E402
from skill_http import usage  # noqa: E402

val = sys.argv[1] if len(sys.argv) > 1 else usage('Usage: li-personal-update-headline.py "Headline text"')
print(f"→ Updating ({len(val)} chars)...")
print_result(http("POST", f"http://localhost:9225/profile/headline",
                  json_body={"headline": val}))
