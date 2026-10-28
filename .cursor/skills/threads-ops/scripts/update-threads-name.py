#!/usr/bin/env python3
"""Update Threads display name via browser bridge.
Usage: update-threads-name.py <username> <name>
Note: Threads allows name changes only twice per 14 days."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import threads_edit_profile  # noqa: E402
from skill_http import usage  # noqa: E402

if len(sys.argv) < 3:
    usage("update-threads-name.py <username> <name>")
username, name = sys.argv[1], sys.argv[2]

print(f"=== Updating Threads name for @{username} to '{name}' ===")
print("WARNING: Threads allows name changes only twice per 14 days.")
threads_edit_profile("Name", username, name)
print("Threads name updated.")
