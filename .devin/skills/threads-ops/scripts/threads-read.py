#!/usr/bin/env python3
"""Threads read path via yarn-threads-cli (cookie-auth, no OAuth).

The official Threads API publishes but cannot browse profiles, search
users, or read arbitrary posts. This wraps the unofficial CLI for those
reads. Auth: THREADS_SESSION_ID + THREADS_CSRF_TOKEN in repo .env —
export them from a browser logged into threads.net (browser-novnc
profile). Runs the CLI via npx
first run downloads the package.

Usage:
  threads-read.py whoami
  threads-read.py about <handle|user-id>
  threads-read.py user-posts <handle> [--limit N]
  threads-read.py read <url|shortcode|post-id>
  threads-read.py replies <url|shortcode>
  threads-read.py search <query>
  threads-read.py likes
Any extra args pass through
--json supported where the CLI offers it.
"""

import os
import subprocess
import sys
from pathlib import Path


def load_env() -> None:
    env_file = Path(__file__).resolve().parents[4] / ".env"
    if not env_file.is_file():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0
    load_env()
    if not os.environ.get("THREADS_SESSION_ID"):
        print(
            "THREADS_SESSION_ID not set — export sessionid + csrf cookies "
            "from a threads.net browser session into .env "
            "(see threads-ops SKILL.md → Threads read path)",
            file=sys.stderr,
        )
        return 2
    cmd = ["npx", "-y", "yarn-threads-cli", *sys.argv[1:]]
    env = {
        **os.environ,
        "THREADS_SESSION_ID": os.environ["THREADS_SESSION_ID"],
        "THREADS_CSRF_TOKEN": os.environ.get("THREADS_CSRF_TOKEN", ""),
    }
    return subprocess.call(cmd, env=env)


if __name__ == "__main__":
    sys.exit(main())
