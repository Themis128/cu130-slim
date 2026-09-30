#!/usr/bin/env python3
"""Save a social secret in the SocialAuto secret store.
Usage: save-secret.py <secret-key> <secret-value> [description]"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import set_secret, usage  # noqa: E402

if len(sys.argv) < 3:
    usage("save-secret.py <secret-key> <secret-value> [description]")
set_secret(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "")
