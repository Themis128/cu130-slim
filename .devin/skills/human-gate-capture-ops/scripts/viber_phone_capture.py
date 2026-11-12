#!/usr/bin/env python3
"""DEPRECATED shim — the real implementation moved to viber-bot-ops.

    ../viber-bot-ops/scripts/viber_phone_capture.py run|devices|probe

Kept so older docs/cron lines keep working; new code should call the
viber-bot-ops path directly (full navigation: search -> bot chat ->
Edit Info -> App Token -> clipboard -> provision).
"""
import os
import subprocess
import sys

REAL = os.path.join(os.path.dirname(__file__), "..", "..",
                    "viber-bot-ops", "scripts", "viber_phone_capture.py")

if __name__ == "__main__":
    sys.exit(subprocess.call([sys.executable, os.path.abspath(REAL)]
                             + sys.argv[1:]))
