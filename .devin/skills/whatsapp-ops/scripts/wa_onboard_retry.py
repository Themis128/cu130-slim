#!/usr/bin/env python3
"""Retry the WhatsApp ON_PREMISE→Cloud onboarding click via the facebook
sidecar until Meta's 'temporarily blocked' clears — then stop for the
human/OTP chain.

Usage: wa_onboard_retry.py [--tries N] [--wait SEC]
  Each try: open the dev-console WhatsApp Business Platform page, click
  Continue, and report success/block/other. On 'onboarding started' it
  STOPS — the OTP chain (request_code → verify_code → register) runs via
  whatsapp_tool.py after that.
"""
import argparse
import json
import time
import urllib.request

SIDECAR = "http://localhost:9226"
APP_ID = "1936126137016578"
URL = f"https://developers.facebook.com/apps/{APP_ID}/whatsapp-business/wa-settings/"


def req(method, path, payload=None, timeout=60):
    r = urllib.request.Request(
        f"{SIDECAR}{path}",
        data=json.dumps(payload).encode() if payload else None,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    return json.loads(urllib.request.urlopen(r, timeout=timeout).read())


def js(script):
    return req("POST", "/debug/eval", {"script": script}, 30).get("result")


def attempt(i):
    req("POST", "/debug/navigate", {"url": URL})
    time.sleep(8)
    click = js(
        "(()=>{const b=[...document.querySelectorAll('div[role=button],button')]"
        ".find(e=>(e.innerText||'').trim()==='Continue');if(!b)return 'no-continue';"
        "b.click();return 'clicked'})()"
    )
    print(f"[{i}] click={click}")
    if click != "clicked":
        return "no-button"
    time.sleep(7)
    txt = req("GET", "/debug/page-text", timeout=20).get("text", "").lower()
    if "temporarily blocked" in txt:
        return "blocked"
    if "onboarding failure" in txt:
        return "failure-other"
    return "progressed"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--tries", type=int, default=6)
    p.add_argument("--wait", type=int, default=900)  # 15 min between tries
    a = p.parse_args()
    for i in range(1, a.tries + 1):
        try:
            r = attempt(i)
            print(f"[{i}] -> {r}")
            if r == "progressed":
                print("ONBOARDING PROGRESSED — run whatsapp_tool.py verify chain next")
                return
        except Exception as e:
            print(f"[{i}] error: {e}")
        if i < a.tries:
            print(f"  sleeping {a.wait}s…")
            time.sleep(a.wait)
    print("all tries still blocked — try from a personal device/browser")


if __name__ == "__main__":
    main()
