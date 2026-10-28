#!/usr/bin/env python3
"""Shared helpers for meta-* scripts."""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import env  # noqa: E402

GRAPH = "https://graph.facebook.com/v21.0"


def graph(path: str, params: dict | None = None) -> dict:
    qs = "&".join(f"{k}={v}" for k, v in (params or {}).items())
    url = f"{GRAPH}{path}?{qs}" if qs else f"{GRAPH}{path}"
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())
    except urllib.error.URLError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:
            return {"error": str(e)}


def fb_token(cli_token: str = "") -> str:
    token = cli_token or env("FACEBOOK_ACCESS_TOKEN")
    if not token:
        print("Error: No access token provided.", file=sys.stderr)
        sys.exit(1)
    return token


def show(data: dict) -> None:
    print(json.dumps(data, indent=2))
