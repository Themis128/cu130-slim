#!/usr/bin/env python3
"""Watch the headed bridge (9223) for a completed TikTok QR login, then
extract cookies and inject them into the tiktok sidecar (9224).
Detection: real tiktok auth cookies (sessionid/sid_tt) — NOT url change.
The tagged evaluate poll also refreshes the tiktok busy-hold so foreign
platform pollers cannot preempt mid-login.
Usage: tiktok_qr_watch.py"""

import json
import sys
import time
import urllib.request
from pathlib import Path

BRIDGE = "http://localhost:9223"
SIDECAR = "http://localhost:9224"
DEADLINE = time.time() + 1500  # 25 min
ROOT = Path(__file__).resolve().parent.parent


def post(url: str, data: dict, platform: str = "tiktok",
         timeout: int = 15) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json",
                 "X-Platform": platform}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception:
        return {}


while time.time() < DEADLINE:
    info = post(f"{BRIDGE}/session/evaluate", {
        "expression": "JSON.stringify({url:location.href,"
                      "title:document.title})"})
    result = str(info.get("result", info))
    print(f"[{time.strftime('%H:%M:%S')}] {result[:160]}")

    if "tiktok.com" in result and "login" not in result.lower():
        ext = post(f"{BRIDGE}/session/extract", {}, timeout=20)
        print(f"[{time.strftime('%H:%M:%S')}] extract: "
              f"{str(ext)[:200]}")
        cookies = ext.get("cookies", {})
        if "sessionid" in cookies or "sid_tt" in cookies:
            print("REAL LOGIN — injecting cookies into sidecar")
            Path("/tmp/tk_extract.json").write_text(json.dumps(ext))
            body = json.dumps({"session_id": cookies.get("sessionid", ""),
                               "cookies": cookies}).encode()
            req = urllib.request.Request(
                f"{SIDECAR}/session", data=body,
                headers={"Content-Type": "application/json"})
            print(urllib.request.urlopen(req, timeout=60)
                  .read().decode())
            sys.exit(0)
    time.sleep(25)

print("TIMEOUT — QR expired or never scanned")
sys.exit(1)
