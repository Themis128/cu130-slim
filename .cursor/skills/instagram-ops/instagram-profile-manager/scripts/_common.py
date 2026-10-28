#!/usr/bin/env python3
"""Shared helpers for instagram-profile-manager scripts."""

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import api_base  # noqa: E402


def sidecar() -> str:
    return api_base("INSTAGRAM_SIDECAR_URL", "http://localhost:8011")


def sc(method: str, path: str, sid: str = "", form: dict | None = None, raw: bool = False):
    headers = {}
    if sid:
        headers["X-Session-ID"] = sid
    body = None
    if form is not None:
        body = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(sidecar() + path, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            text = r.read().decode()
    except urllib.error.URLError as e:
        text = e.read().decode() if hasattr(e, "read") else str(e)
    if raw:
        return text
    try:
        return json.loads(text)
    except ValueError:
        return text


def print_profile(d: dict) -> None:
    print(f'Username: {d.get("username", "N/A")}')
    print(f'Full name: {d.get("full_name", "N/A")}')
    print(f'PK: {d.get("pk", "N/A")}')
    print(f'Biography: {d.get("biography", "N/A")}')
    print(f'External URL: {d.get("external_url", "N/A")}')
    print(f'Followers: {d.get("follower_count", "N/A")}')
    print(f'Following: {d.get("following_count", "N/A")}')
    print(f'Media count: {d.get("media_count", "N/A")}')
    print(f'Is private: {d.get("is_private", "N/A")}')
    print(f'Is business: {d.get("is_business", "N/A")}')
    print(f'Profile pic URL: {str(d.get("profile_pic_url", "N/A"))[:80]}...')
