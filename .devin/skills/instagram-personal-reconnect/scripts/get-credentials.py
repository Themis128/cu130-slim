#!/usr/bin/env python3
"""Retrieve Instagram credentials from SocialAuto's secret store.
Writes username to stdout and password to a temp file (path printed to stderr).

Usage: get-credentials.py <account_id>"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import get_credentials  # noqa: E402
from skill_http import usage  # noqa: E402

account_id = sys.argv[1] if len(sys.argv) > 1 else usage("get-credentials.py <account_id>")
username, password = get_credentials(account_id)

f = tempfile.NamedTemporaryFile(mode="w", prefix="ig-pass-", dir="/tmp",
                                delete=False)
f.write(password)
f.close()
Path(f.name).chmod(0o600)

print(username)
print(f"Password written to: {f.name}", file=sys.stderr)
print(f.name, file=sys.stderr)
