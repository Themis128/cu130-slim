#!/usr/bin/env python3
"""Check env vars for all OAuth platforms.
Usage: check-oauth-env.py"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import repo_root  # noqa: E402

env: dict[str, str] = {}
env_file = repo_root() / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip().rstrip("\r")
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
env.update({k: v for k, v in os.environ.items() if k not in env or not env[k]})


def check_var(name: str) -> None:
    val = env.get(name, "")
    if not val:
        print(f"  MISSING: {name}")
    else:
        print(f"  OK: {name} ({len(val)} chars)")


def check_redirect(name: str) -> None:
    val = env.get(name, "")
    if not val:
        print(f"  MISSING: {name}")
    elif val.startswith("https://"):
        print(f"  OK: {name} ({val})")
    elif val.startswith("http://localhost"):
        print(f"  WARNING: {name} uses localhost ({val}) — Meta/Twitter may reject in production")
    else:
        print(f"  WARNING: {name} has unexpected format ({val})")


GROUPS = [
    ("LinkedIn", ["LINKEDIN_CLIENT_ID", "LINKEDIN_CLIENT_SECRET",
                  "LINKEDIN_REDIRECT_URI"]),
    ("Twitter/X", ["TWITTER_CLIENT_ID", "TWITTER_CLIENT_SECRET",
                   "TWITTER_REDIRECT_URI"]),
    ("Facebook", ["FACEBOOK_CLIENT_ID", "FACEBOOK_CLIENT_SECRET",
                  "FACEBOOK_REDIRECT_URI"]),
    ("Instagram", ["INSTAGRAM_CLIENT_ID", "INSTAGRAM_CLIENT_SECRET",
                   "INSTAGRAM_REDIRECT_URI"]),
    ("Threads", ["THREADS_CLIENT_ID", "THREADS_CLIENT_SECRET",
                 "THREADS_REDIRECT_URI"]),
    ("TikTok", ["TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET",
                "TIKTOK_REDIRECT_URI"]),
]

print("=== OAuth Environment Variables ===\n")
for label, names in GROUPS:
    print(f"--- {label} ---")
    for n in names:
        if n.endswith("REDIRECT_URI"):
            check_redirect(n)
        else:
            check_var(n)
    print()
