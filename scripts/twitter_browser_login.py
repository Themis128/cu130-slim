#!/usr/bin/env python3
"""Drive the X/Twitter two-step login through the browser-novnc bridge.

Usage:
    python3 scripts/twitter_browser_login.py [username]

Reads TWITTER_LOGIN_USERNAME / TWITTER_LOGIN_PASSWORD (and the account
handle as fallback username) from .env. Pauses celery-beat first so the
messenger pollers can't hijack the shared browser mid-login, and unpauses
it afterwards.

X login quirks encoded here (verified Sept 2026):
- x.com/login redirects to /i/jf/onboarding/web?mode=login — that funnel IS
  the login page.
- Enter the USERNAME handle, not the email — an email routes into the
  signup funnel ("Email signups are only allowed on the apps").
- Two steps: username -> Continue -> password -> Continue. Do not fill the
  password on step 1.
- Click the *visible* Continue via real mouse events — the page renders
  duplicate hidden buttons and JS .click() is untrusted.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

BRIDGE = "http://localhost:9223"
ROOT = Path(__file__).resolve().parent.parent


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for line in (ROOT / ".env").read_text().splitlines():
        m = re.match(r"^([A-Z0-9_]+)=(.*)$", line.strip())
        if m:
            env[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return env


def post(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        BRIDGE + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        return json.loads(urllib.request.urlopen(req, timeout=90).read())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode()[:300]}"}


def evaluate(expr: str):
    return post("/session/evaluate", {"expression": expr}).get("result")


def pause_beat(paused: bool) -> None:
    cmd = "pause" if paused else "unpause"
    subprocess.run(
        ["docker", "compose", cmd, "celery-beat"],
        cwd=ROOT,
        capture_output=True,
    )


def click_continue() -> bool:
    res = evaluate(
        """(() => {
            const labels = ['Continue', 'Next', 'Log in', 'Sign in',
                            'Συνέχεια', 'Σύνδεση', 'Επόμενο'];
            const els = [...document.querySelectorAll('button,[role=button],input[type=submit]')];
            const c = els.find(e => {
                const t = (e.innerText || e.value || '').trim();
                return labels.includes(t) && e.offsetParent !== null;
            });
            if (!c) return {found: false};
            const b = c.getBoundingClientRect();
            return {found: true, x: b.x + b.width / 2, y: b.y + b.height / 2};
        })()"""
    )
    if not isinstance(res, dict) or not res.get("found"):
        return False
    post("/session/mouse-click", {"x": res["x"], "y": res["y"]})
    return True


def main() -> int:
    env = load_env()
    username = (
        (sys.argv[1] if len(sys.argv) > 1 else "")
        or env.get("TWITTER_LOGIN_USERNAME")
        or env.get("TWITTER_LOGIN_EMAIL")
        or "TBaltzakis"
    ).lstrip("@")
    password = env.get("TWITTER_LOGIN_PASSWORD", "")
    if not password:
        print("TWITTER_LOGIN_PASSWORD missing from .env", file=sys.stderr)
        return 1

    print("Pausing celery-beat (stops browser-hijacking pollers)...")
    pause_beat(True)
    try:
        print(post("/session/start", {"platform": "twitter", "force": True}))
        time.sleep(8)
        post("/session/navigate", {"url": "https://x.com/i/flow/login"})
        time.sleep(8)

        deadline = time.time() + 120
        while time.time() < deadline:
            if evaluate("() => !!document.querySelector('input[name=username_or_email]')"):
                break
            time.sleep(1)
        else:
            print("login form never rendered")
            return 1

        post("/session/fill", {"selector": "input[name=username_or_email]", "value": username})
        time.sleep(1.5)
        if not click_continue():
            print("Continue button not found (step 1)")
            return 1

        filled = False
        while time.time() < deadline:
            state = evaluate(
                """(() => ({
                    pwReady: !![...document.querySelectorAll('input[name=password]')]
                        .find(i => !i.disabled && i.offsetParent !== null),
                    arkose: !!document.querySelector('iframe[src*=arkose],[id*=arkose]'),
                    err: document.body.innerText.includes('password you entered is incorrect'),
                }))()"""
            )
            if isinstance(state, dict):
                if state.get("arkose"):
                    print("arkose captcha — log in manually via http://localhost:6080/vnc.html")
                    return 2
                if state.get("err"):
                    print("x.com rejected the password — update TWITTER_LOGIN_PASSWORD in .env")
                    return 1
                if state.get("pwReady"):
                    post("/session/fill", {"selector": "input[name=password]", "value": password})
                    filled = True
                    break
            time.sleep(1)
        if not filled:
            print("password step never rendered")
            return 1

        time.sleep(1.5)
        if not click_continue():
            print("Continue button not found (step 2)")
            return 1

        while time.time() < deadline:
            state = evaluate(
                "() => ({url: location.href, loggedIn: !!document.querySelector("
                "'[data-testid=SideNav_AccountSwitcher_Button]')})"
            )
            if isinstance(state, dict) and state.get("loggedIn"):
                print(f"LOGGED IN — {state.get('url')}")
                post("/session/extract", {})
                return 0
            time.sleep(2)
        print("login did not complete before timeout")
        return 1
    finally:
        print("Resuming celery-beat...")
        pause_beat(False)


if __name__ == "__main__":
    sys.exit(main())
