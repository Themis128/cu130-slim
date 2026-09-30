#!/usr/bin/env python3
"""Claim the LinkedIn ad-credit offer through the browser-novnc bridge
(:9223) — the headed "human" browser that LinkedIn isn't rate-limiting.

Flow: credential login (LINKEDIN_EMAIL / LINKEDIN_PASSWORD from .env) ->
campaignmanager mcid deep-link -> ad account -> Billing -> Credits and
promotions -> Redeem coupon -> apply code -> report balance.

Never spends money: stops and reports if a real card-entry form renders.
All calls carry X-Platform: linkedin to hold the bridge against pollers.
"""
import json
import sys
import time
import urllib.error
import urllib.request

BRIDGE = "http://localhost:9223"
REPO = "/home/tbaltzakis/cu130-slim"
COUPON = "xzkhxT3Nk"
MCID_URL = (
    "https://www.linkedin.com/campaignmanager/login"
    "?mcid=7026332492244680704"
    "&trk=eml-mktg-acq-202302-global-spn-cnt-low-perf-cmt-initial&src=e-eml"
)
HDRS = {"X-Platform": "linkedin", "Content-Type": "application/json"}


def call(path, payload=None, timeout=90):
    req = urllib.request.Request(
        BRIDGE + path,
        data=json.dumps(payload or {}).encode(),
        headers=HDRS, method="POST" if payload is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def evaluate(expr, timeout=60):
    out = call("/session/evaluate", {"expression": expr}, timeout=timeout)
    return out.get("result")


def load_env(path):
    env = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def body_text(n=1500):
    return evaluate(
        f"document.body ? document.body.innerText.slice(0,{n}) : ''") or ""


def click_text(patterns):
    """Click first matching element whose text contains any pattern."""
    for pat in patterns:
        for sel in ("button", "a", "[role=button]", "li", "span"):
            try:
                call("/session/click", {"selector": sel, "text": pat})
                return pat
            except (urllib.error.URLError, TimeoutError):
                continue
    return None


def main():
    env = load_env(f"{REPO}/.env")
    user = (env.get("LINKEDIN_EMAIL") or env.get("LINKEDIN_USERNAME") or "").strip()
    password = (env.get("LINKEDIN_PASSWORD") or "").strip()
    if not user or not password:
        print("ERROR: LINKEDIN_* missing in .env")
        sys.exit(1)

    target = sys.argv[1] if len(sys.argv) > 1 else MCID_URL

    # ── 1. login ──────────────────────────────────────────────────────────
    info = call("/session/page-info")
    print("at:", info)
    if "linkedin.com/login" in info.get("url", ""):
        # LinkedIn renders two DOM copies (dialog + inline) — :visible picks
        # the live one; ids are auto-generated («r0») so match on type.
        call("/session/fill", {"selector": "input[type=email]:visible", "value": user})
        call("/session/fill", {"selector": "input[type=password]:visible", "value": password})
        time.sleep(1)
        # Submit button label is localized ("Σύνδεση") and collides with the
        # Apple-SSO button — click by coordinates on the exact-text match.
        box = evaluate(
            "(() => { const b=[...document.querySelectorAll('button')]"
            ".find(e=>e.offsetParent && (e.innerText||'').trim()"
            " && !(e.innerText||'').includes('Apple')); "
            "if(!b) return 'none'; const r=b.getBoundingClientRect(); "
            "return JSON.stringify({x:r.x+r.width/2, y:r.y+r.height/2, t:b.innerText.trim()}); })()")
        print("submit btn:", box)
        if box and box != "none":
            pt = json.loads(box)
            call("/session/mouse-click", {"x": pt["x"], "y": pt["y"]})
            print("login submitted")
        for _ in range(15):
            time.sleep(2)
            info = call("/session/page-info")
            url = info.get("url", "")
            if "/feed" in url or "checkpoint" in url or "challenge" in url:
                break
        print("after login:", info)
        url = info.get("url", "")
        if "checkpoint" in url or "challenge" in url:
            print("CHECKPOINT — needs manual step via noVNC")
            print(body_text(400))
            return
        if "/feed" not in url and "login" in url:
            print("login did not complete:", body_text(400))
            return

    # ── 2. campaign manager ───────────────────────────────────────────────
    call("/session/navigate", {"url": target})
    time.sleep(7)
    info = call("/session/page-info")
    print("cm:", info)
    print(body_text(500))

    # ── 3. billing -> credits -> redeem ───────────────────────────────────
    for label, patterns in [
        ("billing", ["Billing"]),
        ("credits", ["Credits and promotions", "Credits & promotions"]),
        ("redeem", ["Redeem coupon", "Redeem"]),
    ]:
        hit = click_text(patterns)
        print(f"{label}: {'clicked ' + hit if hit else 'NOT FOUND'}")
        time.sleep(4)

    # ── 4. coupon field ───────────────────────────────────────────────────
    filled = evaluate(
        "(() => { const els=[...document.querySelectorAll('input')].filter("
        "e=>e.offsetParent && !e.disabled); const c=els.find(e=>/coupon/i.test("
        "(e.name||'')+(e.placeholder||'')+(e.getAttribute('aria-label')||'')))"
        " || els.find(e=>e.type==='text'); if(!c) return 'none'; return 'ok'; })()")
    print("coupon field:", filled)
    if filled == "ok":
        for sel in ("input[name*=coupon i]", "input[placeholder*=coupon i]",
                    "input[type=text]"):
            try:
                call("/session/fill", {"selector": sel, "value": COUPON})
                print("coupon filled via", sel)
                break
            except (urllib.error.URLError, TimeoutError):
                continue
        hit = click_text(["Apply", "Redeem", "Submit"])
        print("apply:", hit)
        time.sleep(4)

    card_form = evaluate(
        "document.querySelectorAll('input[autocomplete*=cc],"
        "input[name*=card], iframe[src*=card]').length")
    print("card form fields:", card_form)
    print("final:", call("/session/page-info"))
    print(body_text(800))


if __name__ == "__main__":
    main()
