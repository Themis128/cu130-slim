#!/usr/bin/env python3
"""gate_runner2.py — end-to-end gate runner over the Windows bridge.

Chains: profile-clone (bat on Windows) → debug launch → CDP forwarder →
gate-tab open → session watch → auto-click. Run from WSL:

    python3 gate_runner2.py                 # full chain + watch 4h
    python3 gate_runner2.py --no-watch      # just clone + launch + tabs
    python3 gate_runner2.py --eval URL EXPR # raw CDP eval on a tab

Env: WIN_AGENT (default http://100.83.120.93:8765), CDP (default :9223).

Learned traps baked in (2026-10-29):
- Chrome/Edge >=136 refuse remote-debugging on the REAL profile dir →
  clone to C:\\tmp\\*-full and debug the clone. DPAPI cookies decrypt
  same-machine/same-user, but sessions may still be dead server-side.
- Copy the WHOLE 'User Data' (robocopy /E minus caches) — cookies-only
  clones fingerprint-mismatch and Meta/X still wall.
- SQLite WAL: copy Cookies* while the source browser is CLOSED or the
  newest session cookies never land.
- LenovoVantage WebView2 steals :9222 — kill msedgewebview2.exe and
  verify targets include edge://newtab / chrome://newtab before driving.
- X's 'Continue with Google' lives in an accounts.google.com/gsi
  iframe — attach to the iframe target, don't search the parent DOM.
- Facebook m.facebook.com 'not available on this browser' = wrong UA/
  wrong target (WebView2), not a real block.
- WS needs suppress_origin (Chrome rejects non-localhost Origin).
"""
import base64
import json
import os
import sys
import time
import urllib.request

AGENT = os.environ.get("WIN_AGENT", "http://100.83.120.93:8765")
CDP = os.environ.get("CDP", "http://100.83.120.93:9223")
GATES = [
    "https://business.facebook.com/wa/manage/home/",
    "https://x.com/i/flow/login",
]

BROWSER = "chrome"   # 'chrome' or 'edge'
PROFILE_DIR = {"chrome": r"Google\Chrome\User Data",
               "edge": r"Microsoft\Edge\User Data"}
EXE = {"chrome": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
       "edge": r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"}
PROC = {"chrome": "chrome.exe", "edge": "msedge.exe"}
CLONE = {"chrome": r"C:\tmp\chrome-full", "edge": r"C:\tmp\edge-full"}


def agent_exec(cmd: str, timeout: int = 40) -> dict:
    req = urllib.request.Request(
        AGENT + "/exec",
        data=json.dumps({"cmd": cmd, "timeout": timeout}).encode(),
        headers={"Content-Type": "application/json"})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=timeout + 10).read())
    except Exception as e:
        return {"err": str(e)}


def clone_and_launch(browser: str = BROWSER) -> None:
    bat = (
        "@echo off\r\n"
        f"taskkill /f /im {PROC[browser]} 2>nul\r\n"
        "taskkill /f /im msedgewebview2.exe 2>nul\r\n"
        "timeout /t 4 /nobreak >nul\r\n"
        f"robocopy \"%LOCALAPPDATA%\\{PROFILE_DIR[browser]}\" {CLONE[browser]} /E "
        "/XD \"Cache\" \"Code Cache\" \"GPUCache\" \"Service Worker\\CacheStorage\" Crashpad "
        "/XF Singleton* *.tmp /R:1 /W:1 /NFL /NDL /NJH >nul\r\n"
        f"start \"\" \"{EXE[browser]}\" --remote-debugging-port=9222 "
        "--remote-allow-origins=* "
        f"--user-data-dir={CLONE[browser]} " + " ".join(GATES) + "\r\n")
    b64 = base64.b64encode(bat.encode()).decode()
    agent_exec(
        "powershell -Command \"[IO.File]::WriteAllBytes('C:/tmp/gate_clone.bat',"
        f"[Convert]::FromBase64String('{b64}'))\" && start /min cmd /c C:\\tmp\\gate_clone.bat",
        timeout=20)


def pages() -> list:
    try:
        return json.load(urllib.request.urlopen(CDP + "/json", timeout=8))
    except Exception:
        return []


def eval_tab(t, expr: str):
    import websocket
    ws = websocket.create_connection(
        t["webSocketDebuggerUrl"], timeout=15, suppress_origin=True)
    ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                        "params": {"expression": expr, "returnByValue": True}}))
    while True:
        r = json.loads(ws.recv())
        if r.get("id") == 1:
            ws.close()
            return r.get("result", {}).get("result", {}).get("value")


def body(t) -> str:
    return eval_tab(t, "document.body?document.body.innerText.slice(0,4000):''") or ""


def click_text(t, pattern: str):
    return eval_tab(t, """(() => {
      const re = /""" + pattern + """/i;
      for (const e of document.querySelectorAll('button,[role=button],a')) {
        const s=(e.innerText||'').trim()
        if (s && s.length<50 && re.test(s) && e.offsetParent) {e.click();return 'clicked:'+s}
      } return 'not found'
      })()""")


def gsi_click() -> bool:
    """Click 'Continue with Google' inside the accounts.google.com/gsi iframe."""
    for t in pages():
        if "accounts.google.com/gsi" in t.get("url", ""):
            r = eval_tab(t, """(() => {
              const b=[...document.querySelectorAll('div[role=button],button')][0]
              if(b){b.click();return 'clicked'} return 'none'})()""")
            print("gsi:", r)
            return r == "clicked"
    return False


def watch_x(timeout_s: int = 14400) -> None:
    """Watch for X login → auto-approve OAuth consent if present."""
    print("[*] watching for X session…")
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        ts = [t for t in pages() if t.get("type") == "page"
              and ("x.com" in t.get("url", "") or "twitter" in t.get("url", ""))]
        for t in ts:
            try:
                txt = body(t).lower()
            except Exception:
                continue
            if "authorize" in txt or "allow" in txt:
                r = click_text(t, "authorize|approve|allow|continue")
                print("oauth click:", r)
                if r != "not found":
                    print("X_GATE_DONE")
                    return
            elif "sign in" not in txt and "log in" not in txt and txt.strip():
                print("[+] X session live")
                return
        if int(time.time() - t0) % 300 < 20:
            gsi_click()   # retry the Google button periodically
        time.sleep(20)
    print("x-watch timeout")


def watch(timeout_s: int = 14400) -> None:
    print("[*] watching for Meta login…")
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        ts = [t for t in pages() if t.get("type") == "page"
              and "facebook" in t.get("url", "")]
        if ts:
            try:
                txt = body(ts[0]).lower()
            except Exception:
                time.sleep(15)
                continue
            if "must log in" not in txt and "log into facebook" not in txt:
                print("[+] session live — hunting consent")
                for _ in range(30):
                    ts = [t for t in pages() if t.get("type") == "page"]
                    t = next((x for x in ts if "facebook" in x.get("url", "")),
                             ts[0] if ts else None)
                    if not t:
                        break
                    try:
                        r = click_text(t, "accept|agree|continue|get started|review")
                        print("click:", r)
                        if r != "not found":
                            print("GATE_DONE")
                            return
                    except Exception as e:
                        print("err:", e)
                    time.sleep(10)
                return
        time.sleep(20)
    print("timeout — no login")


def main() -> int:
    no_watch = "--no-watch" in sys.argv
    if "--eval" in sys.argv:
        i = sys.argv.index("--eval")
        for t in pages():
            if sys.argv[i + 1] in t.get("url", "") and t["type"] == "page":
                print(eval_tab(t, sys.argv[i + 2]))
                return 0
        print("no matching tab")
        return 1
    print("[1/3] clone + launch (robocopy on Windows takes ~40s)")
    clone_and_launch()
    print("[2/3] waiting for CDP + real targets")
    for _ in range(30):
        ps = pages()
        if any(t["type"] == "page" and ("facebook" in t.get("url", "")
               or "x.com" in t.get("url", "")) for t in ps):
            break
        time.sleep(4)
    real = [t for t in pages() if t["type"] == "page"]
    for t in real:
        print("  tab:", t["url"][:80])
    if not real:
        print("no tabs — check win_agent/Chrome")
        return 1
    print("[3/3] " + ("armed watch" if not no_watch else "done (no-watch)"))
    if not no_watch:
        import threading
        threading.Thread(target=watch_x, daemon=True).start()
        watch()
    return 0


if __name__ == "__main__":
    sys.exit(main())
