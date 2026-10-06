#!/usr/bin/env python3
"""Set a secret in the SocialAuto secret store.
Usage: set-secret.py <secret-key> <secret-value> [description]"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import set_secret, usage  # noqa: E402

if len(sys.argv) < 3:
    usage("set-secret.py <secret-key> <secret-value> [description]")
set_secret(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "")
