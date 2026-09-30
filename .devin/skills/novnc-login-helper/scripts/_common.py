"""Shared helpers for novnc-login-helper scripts."""

import json
import urllib.request
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))

BRIDGE = "http://localhost:9223"

LOGIN_URLS = {
    "twitter": "https://x.com/i/flow/login",
    "x": "https://x.com/i/flow/login",
    "threads": "https://www.threads.com/login/",
    "tiktok": "https://www.tiktok.com/login/phone-or-email/email",
    "instagram": "https://www.instagram.com/accounts/login/",
}

CHECK_URLS = {
    "twitter": "https://x.com/home",
    "x": "https://x.com/home",
    "threads": "https://www.threads.com/",
    "tiktok": "https://www.tiktok.com/foryou",
    "instagram": "https://www.instagram.com/",
}

LOGIN_CHECKS = {
    "twitter": '!document.querySelector("a[href*=\\"login\\"]") && '
               '(document.querySelector("div[data-testid=\\"SideNav_NewTweet_Button\\"], '
               'a[href=\\"/compose/post\\"], nav[aria-label=\\"Primary\\"]") !== null)',
    "x": '!document.querySelector("a[href*=\\"login\\"]") && '
         '(document.querySelector("div[data-testid=\\"SideNav_NewTweet_Button\\"], '
         'a[href=\\"/compose/post\\"], nav[aria-label=\\"Primary\\"]") !== null)',
    "threads": '!document.querySelector("a[href*=\\"login\\"]") && '
               'document.body.innerText.includes("Messages")',
    "tiktok": 'document.querySelector("[data-e2e=\\"profile-icon\\"], '
              'a[href*=\\"/profile\\"]") !== null',
    "instagram": '!document.querySelector("a[href=\\"/accounts/login/\\"]")',
}


def post(path: str, data: dict) -> dict:
    req = urllib.request.Request(
        f"{BRIDGE}{path}", data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except Exception:
        return {}


def check_logged_in(platform: str) -> bool:
    expr = (f'(() => {{ try {{ return {LOGIN_CHECKS[platform]}; }} '
            'catch(e) { return false; } })()')
    result = post("/session/evaluate", {"expression": expr}).get("result")
    return result in (True, "true", "True")
