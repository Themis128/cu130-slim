#!/usr/bin/env python3
"""Diagnose LinkedIn article-editor hydration on a given browser target.

Probes: session state → navigate /article/new/ → poll for ProseMirror →
report timing, redirect loops, and failure signature. Use before burning a
full publish attempt, or when the n8n weekly run reports editor timeouts.

Usage:
  probe_editor.py --target bridge          # browser-novnc :9223 (default, verified)
  probe_editor.py --target sidecar         # linkedin-browser-sidecar :9225
  probe_editor.py --target sidecar --timeout 300
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import api_base, request  # noqa: E402

EDITOR_URL = "https://www.linkedin.com/article/new/"
PM_CHECK = ('JSON.stringify({u:location.href.slice(0,70),'
            'pm:!!document.querySelector("div.ProseMirror[contenteditable=true]"),'
            't:(document.title||"").slice(0,50),'
            'body:(document.body?document.body.innerText.length:0)})')


def probe_bridge(timeout: int):
    base = api_base("BROWSER_BRIDGE_URL", "http://localhost:9223")
    h = {"X-Platform": "linkedin"}

    print("== bridge health ==")
    print(request("GET", f"{base}/health"))

    print("\n== claiming linkedin session (force) ==")
    print(request("POST", f"{base}/session/start",
                  data={"platform": "linkedin", "interactive": True, "force": True},
                  headers=h, timeout=60))
    time.sleep(6)

    st = request("GET", f"{base}/session/status")
    print("session:", st)
    if "li_at" not in str(st.get("cookies_found", [])) and st.get("status") != "done":
        print("!! no li_at — log in via noVNC first (http://localhost:6080/vnc.html)")
        return 2

    print("\n== navigate /article/new/ ==")
    request("POST", f"{base}/session/navigate", data={"url": EDITOR_URL},
            headers=h, timeout=90)

    print(f"polling for ProseMirror (≤{timeout}s)…")
    deadline, t0, last = time.time() + timeout, time.time(), None
    urls = []
    while time.time() < deadline:
        try:
            d = request("POST", f"{base}/session/evaluate",
                        data={"expression": PM_CHECK}, headers=h, timeout=20)
            state = json.loads(d.get("result") or "{}")
            urls.append(state.get("u"))
            if state != last:
                print(f"  [{time.time()-t0:5.0f}s] {state}")
                last = state
            if state.get("pm"):
                print(f"\nEDITOR UP in {time.time()-t0:.0f}s")
                return 0
        except Exception as e:
            print(f"  [{time.time()-t0:5.0f}s] eval err: {e}")
        time.sleep(5)
    bounces = len(urls) - len(set(urls))
    print(f"\nTIMEOUT after {timeout}s — {bounces} url changes observed")
    print("signature: splash loop / flagged profile → use bridge path "
          "or re-auth the sidecar session")
    return 1


def probe_sidecar(timeout: int):
    base = api_base("LINKEDIN_SIDECAR_URL", "http://localhost:9225")

    print("== sidecar health ==")
    print(request("GET", f"{base}/health"))

    print("\n== session ==")
    print(request("GET", f"{base}/session", timeout=60))

    print("\n== debug-navigate /article/new/ ==")
    print(request("POST", f"{base}/debug/navigate", data={"url": EDITOR_URL},
                  timeout=90))

    print(f"polling for ProseMirror (≤{timeout}s)…")
    deadline, t0, last = time.time() + timeout, time.time(), None
    while time.time() < deadline:
        try:
            d = request("POST", f"{base}/debug/eval",
                        data={"script": f"(()=>({PM_CHECK}))()"}, timeout=20)
            state = json.loads(d.get("result") or "{}")
            if state != last:
                print(f"  [{time.time()-t0:5.0f}s] {state}")
                last = state
            if state.get("pm"):
                print(f"\nEDITOR UP in {time.time()-t0:.0f}s")
                return 0
        except Exception as e:
            print(f"  [{time.time()-t0:5.0f}s] eval err: {e}")
        time.sleep(5)
    print(f"\nTIMEOUT after {timeout}s — editor never mounted")
    print("check: docker stats linkedin-browser-sidecar (oom_kill?); "
          "headed+Xvfb env; /data/tmp/article-editor-dead-* screenshots")
    return 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", choices=["bridge", "sidecar"], default="bridge")
    ap.add_argument("--timeout", type=int, default=180)
    args = ap.parse_args()
    fn = probe_bridge if args.target == "bridge" else probe_sidecar
    sys.exit(fn(args.timeout))


if __name__ == "__main__":
    main()
