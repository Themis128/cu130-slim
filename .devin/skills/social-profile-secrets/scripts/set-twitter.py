#!/usr/bin/env python3
"""Save Twitter/X API credentials.
Usage: set-twitter.py <api_key> <api_secret> <access_token> <access_token_secret>"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import set_secret, usage  # noqa: E402

if len(sys.argv) < 5:
    usage("set-twitter.py <api_key> <api_secret> <access_token> <access_token_secret>")
set_secret("TWITTER_API_KEY", sys.argv[1], "Twitter/X API key")
set_secret("TWITTER_API_SECRET", sys.argv[2], "Twitter/X API secret")
set_secret("TWITTER_ACCESS_TOKEN", sys.argv[3], "Twitter/X access token")
set_secret("TWITTER_ACCESS_TOKEN_SECRET", sys.argv[4], "Twitter/X access token secret")
