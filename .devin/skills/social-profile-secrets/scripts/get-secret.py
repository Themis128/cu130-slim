#!/usr/bin/env python3
"""Get a raw secret value from the SocialAuto secret store.
Usage: get-secret.py <secret-key>"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import get_secret, usage  # noqa: E402

key = sys.argv[1] if len(sys.argv) > 1 else usage("get-secret.py <secret-key>")
# Deliberate: this tool exists to return a secret to the caller (like `vault
# read -field`), not to log it. Values are never written to files or repos.
print(get_secret(key))  # codeql[py/clear-text-logging-sensitive-data]
