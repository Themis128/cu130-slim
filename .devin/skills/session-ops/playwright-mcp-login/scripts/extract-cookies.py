#!/usr/bin/env python3
"""Extract cookies from the Playwright MCP browser session.

Usage:
  extract-cookies.py <platform>   (twitter, tiktok, threads, instagram)

Outputs cookie JSON to stdout. The agent should call browser_evaluate
via the Playwright MCP server to get document.cookie, then this script
formats it. For HTTP-only cookies, the browser-novnc bridge
/session/cookies endpoint is used after navigation."""

import sys
from pathlib import Path

DOMAINS = {
    "twitter": ".x.com", "x": ".x.com", "tiktok": ".tiktok.com",
    "threads": ".threads.com", "instagram": ".instagram.com",
}

platform = sys.argv[1] if len(sys.argv) > 1 else ""
if platform not in DOMAINS:
    print("Usage: extract-cookies.py <platform>", file=sys.stderr)
    print("Platforms: twitter, tiktok, threads, instagram", file=sys.stderr)
    sys.exit(1)

print(f"""=== Cookie extraction for {platform} ===
Domain: {DOMAINS[platform]}

The agent should use the Playwright MCP browser_evaluate tool to run:
  () => document.cookie

Then parse the cookie string and output as JSON.

For HTTP-only cookies, navigate the browser-novnc bridge to the platform
and use GET /session/cookies to extract the full cookie jar.

Example output format:
[
  {{"name": "auth_token", "value": "...", "domain": "{DOMAINS[platform]}", "path": "/", "secure": true, "httpOnly": true}},
  {{"name": "ct0", "value": "...", "domain": "{DOMAINS[platform]}", "path": "/", "secure": true, "httpOnly": false}}
]""", file=sys.stderr)
