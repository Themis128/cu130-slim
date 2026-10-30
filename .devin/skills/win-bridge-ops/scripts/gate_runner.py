#!/usr/bin/env python3
"""gate_runner.py — run the gate sequence from WSL via the Windows agent."""
import json
import os
import subprocess
import sys
import time
import urllib.request

TOKEN = os.environ.get("WIN_AGENT_TOKEN", "")
REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
CDP_CLICK = os.path.join(REPO, ".devin/skills/gates-ops/scripts/cdp_click.py")


def _host_ip():
    out = subprocess.run(["ip", "route", "show", "default"],
                         capture_output=True, text=True).stdout
    for tok in out.split():
        if tok.count(".") == 3:
            return tok
    return "127.0.0.1"


def _call(base, path, body=None):
    req = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"X-Agent-Token": TOKEN, "Content-Type": "application/json"},
        method="POST" if body is not None else "GET")
    try:
        return json.loads(urllib.request.urlopen(req, timeout=90).read())
    except Exception as e:
        return {"ok": False, "err": f"{type(e).__name__}: {e}"}


def _cdp_up():
    try:
        urllib.request.urlopen("http://localhost:9222/json/version", timeout=3)
        return True
    except Exception:
        return False


def main() -> int:
    base = f"http://{_host_ip()}:8765"
    print(f"agent: {base}")
    h = _call(base, "/health")
    if not h.get("ok"):
        print("AGENT DOWN — run win_agent.py on Windows once (see SKILL.md)")
        return 1
    print("agent: up")

    if not _cdp_up():
        print("launching debug chrome…")
        _call(base, "/launch-chrome")
        for _ in range(15):
            time.sleep(2)
            if _cdp_up():
                break
    print("chrome :9222:", "up" if _cdp_up() else "not up — open it once manually")

    if _cdp_up() and os.path.exists(CDP_CLICK):
        r = subprocess.run(
            [sys.executable, CDP_CLICK,
             "https://business.facebook.com/wa/manage/home/",
             "Accept|Agree|Review|Get started", "8"],
            capture_output=True, text=True, timeout=60)
        print("wa-terms:", (r.stdout or r.stderr).strip()[:160])
    adb = _call(base, "/adb", {"args": ["devices"]})
    print("adb:", (adb.get("out") or adb.get("err", "?")).strip()[:120])
    print("done — see gates-ops SKILL.md for the per-gate map")
    return 0


if __name__ == "__main__":
    sys.exit(main())
