#!/usr/bin/env python3
"""Update Threads profile bio via browser bridge.
Usage: update-threads-bio.py <username> <bio>"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import threads_edit_profile  # noqa: E402
from skill_http import usage  # noqa: E402

if len(sys.argv) < 3:
    usage("update-threads-bio.py <username> <bio>")
username, bio = sys.argv[1], sys.argv[2]

print(f"=== Updating Threads bio for @{username} ===")
threads_edit_profile("Bio", username, bio)
print("Threads bio updated.")
