#!/usr/bin/env python3
"""Shared helpers for threads-ops scripts."""

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import api_base, api_login, request  # noqa: E402

TEAM_ID = "88e2bab4-3581-4c04-b0ac-87aa27840025"
BRIDGE = api_base("BROWSER_BRIDGE_URL", "http://localhost:9223")


def api() -> str:
    return api_base("SOCIAL_API_URL", "http://127.0.0.1:8083")


def team_token() -> tuple[str, str]:
    """Login + switch to the cloudless team. Returns (api_base, token)."""
    base = api()
    token = api_login(base)
    switched = request("POST", f"{base}/api/v1/auth/switch-team",
                       token=token, data={"team_id": TEAM_ID})
    token = switched.get("access_token", "")
    if not token:
        print(f"Could not switch to team {TEAM_ID}", file=sys.stderr)
        sys.exit(1)
    return base, token


def accounts(token: str) -> list[dict]:
    data = request("GET", f"{api()}/api/v1/accounts", token=token)
    return data if isinstance(data, list) else data.get("accounts", data.get("data", []))


def threads_account(token: str, active_only: bool = False) -> dict | None:
    for a in accounts(token):
        if a.get("platform") == "threads" and (not active_only or a.get("status") == "active"):
            return a
    return None


def bridge(path: str, data: dict) -> dict:
    req = urllib.request.Request(
        f"{BRIDGE}{path}", data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.URLError:
        return {}


def evaluate(expr: str) -> dict:
    return bridge("/session/evaluate", {"expression": expr})


def threads_edit_profile(section: str, username: str, value: str) -> None:
    """Shared flow: navigate → Edit profile → click section → fill → Done ×2."""
    bridge("/session/navigate", {"url": f"https://www.threads.com/@{username}"})
    time.sleep(3)
    evaluate("(() => { const btns = document.querySelectorAll(\"div[role=button]\"); "
             "for (const btn of btns) { if (btn.textContent.trim() === \"Edit profile\") "
             "{ btn.click(); return \"clicked\"; } } return \"not found\"; })()")
    time.sleep(2)
    evaluate(f"(() => {{ const dialog = document.querySelector(\"[role=dialog]\"); "
             f"if (!dialog) return \"no dialog\"; const all = dialog.querySelectorAll(\"div[role=button]\"); "
             f"for (const el of all) {{ if (el.textContent.trim().startsWith(\"{section}\")) "
             "{ el.click(); return \"clicked\"; } } return \"not found\"; })()")
    time.sleep(2)
    bridge("/session/fill", {"selector": "textarea", "value": value})
    time.sleep(1)
    done_js = ("(function() { const all = Array.from(document.querySelectorAll("
               "\"div[role=button], button\")); const done = all.filter(b => "
               "b.innerText.trim() === \"Done\"); if (done.length > 0) { "
               "done[done.length - 1].click(); return \"clicked\"; } "
               "return \"no Done\"; })()")
    evaluate(done_js)
    time.sleep(2)
    evaluate(done_js)
    time.sleep(3)
