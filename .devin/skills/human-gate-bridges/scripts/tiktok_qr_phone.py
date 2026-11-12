#!/usr/bin/env python3
"""tiktok_qr_phone.py — close the TikTok QR-login gate without a camera.

The TikTok reauth lane parks on a QR login page in the headed bridge
(:9223). The QR encodes a login URL; instead of asking the human to scan
it, this script:

  1. waits for the bridge page to show the QR login (or --now forces it),
  2. screenshots the viewport via /session/screenshot,
  3. decodes the QR payload (zxing-cpp in the backend venv, cv2 fallback),
  4. if the payload is an https URL, fires an Android VIEW intent on the
     phone over ADB -> the phone's browser opens the TikTok login-confirm
     page -> the human only taps "Confirm" (or the session completes if
     already authorized),
  5. hands off to scripts/tiktok_qr_watch.py which detects the real
     sessionid/sid_tt cookies and injects them into the :9224 sidecar.

    tiktok_qr_phone.py            # wait for QR page, decode, fire intent
    tiktok_qr_phone.py --now      # act on whatever page is open now
    tiktok_qr_phone.py --decode-only   # just report the payload

Env: TK_ADB_SERIAL (default 192.168.1.77:5555 — the Galaxy S22+).
If the QR payload is NOT a URL (opaque token), the phone-browser path is
impossible — the script says so and the manual scan fallback stands.
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

BRIDGE = os.environ.get("TK_QR_BRIDGE", "http://localhost:9223")
ADB_SERIAL = os.environ.get("TK_ADB_SERIAL", "192.168.1.77:5555")
QR_WATCH = "/home/tbaltzakis/cu130-slim/scripts/tiktok_qr_watch.py"
DEADLINE = time.time() + 600  # 10 min for the QR page to appear


def _get(path: str, timeout: int = 20) -> bytes:
    return urllib.request.urlopen(BRIDGE + path, timeout=timeout).read()


def _page_info() -> dict:
    try:
        return json.loads(_get("/session/page-info", timeout=10))
    except Exception:
        return {}


def _looks_like_qr(info: dict) -> bool:
    url = (info.get("url") or "").lower()
    title = (info.get("title") or "").lower()
    return ("tiktok" in url or "tiktok" in title) and (
        "login" in url or "login" in title or "qr" in url + title)


def _decode_qr(png: bytes):
    """Return decoded text or None. Try zxing-cpp (backend venv) then cv2."""
    sys.path.insert(0, "/home/tbaltzakis/cu130-slim/"
                       "social-automation/backend/.venv/lib/python3.12/"
                       "site-packages")
    try:
        import zxingcpp
        res = zxingcpp.read_barcodes(png)
        if res:
            return res[0].text
    except Exception:
        pass
    try:
        import cv2
        import numpy as np
        img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
        det = cv2.QRCodeDetector()
        data, _, _ = det.detectAndDecode(img)
        if data:
            return data
    except Exception:
        pass
    return None


def _adb(*args, timeout: int = 20) -> str:
    r = subprocess.run(["adb", "-s", ADB_SERIAL] + list(args),
                       capture_output=True, text=True, timeout=timeout)
    return (r.stdout + r.stderr).strip()


def _fire_view_intent(url: str) -> bool:
    out = _adb("shell", "am", "start", "-a",
               "android.intent.action.VIEW", "-d", url)
    print(f"adb view-intent -> {url[:90]}")
    print(" ", out[:160])
    return "Error" not in out and "Exception" not in out


def _wait_for_qr_page() -> dict:
    while time.time() < DEADLINE:
        info = _page_info()
        if _looks_like_qr(info):
            return info
        print(f"[{time.strftime('%H:%M:%S')}] waiting for QR page — "
              f"{(info.get('url') or '?')[:80]}")
        time.sleep(15)
    return {}


def main() -> int:
    decode_only = "--decode-only" in sys.argv
    if "--now" in sys.argv:
        info = _page_info()
    else:
        info = _wait_for_qr_page()
    if not info:
        print("no QR login page on the bridge within deadline")
        return 2
    print("page:", info.get("url", "")[:120])

    png = _get("/session/screenshot", timeout=30)
    payload = _decode_qr(png)
    if not payload:
        print("QR decode failed — screenshot needs a manual scan; "
              "payload may be non-QR or image too small")
        return 3
    print("QR payload:", payload[:140])
    if decode_only:
        return 0

    if not payload.lower().startswith(("http://", "https://")):
        print("payload is not a URL — the phone-browser path cannot open "
              "it; manual QR scan required")
        return 4

    if _adb("get-state") != "device":
        print(f"adb device {ADB_SERIAL} not reachable — "
              f"open the URL manually on the phone")
        return 5
    _fire_view_intent(payload)

    # hand off: the watcher catches sessionid/sid_tt and seeds the sidecar
    print("watcher armed — run:")
    print(f"  python3 {QR_WATCH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
