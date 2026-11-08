"""Lightweight session keepalive: reload the IDIKA LandingPage so the
ASP.NET/GSIS server-side sessions don't idle out. Run every ~20 min."""
import json, time, urllib.request, urllib.parse, sys, os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from login import _targets, _new_tab, Tab, URL
from watch import ensure_browser


def main():
    try:
        ensure_browser()  # revive debug Chrome if it ever got closed
    except Exception as e:
        print("ensure_browser:", e)
        return
    t = next((x for x in _targets()
              if x.get("type") == "page" and "idika.gr" in x.get("url", "")), None)
    if t is None:
        t = _new_tab(URL)
        time.sleep(6)
    tab = Tab(t["webSocketDebuggerUrl"])
    try:
        tab.cmd("Page.enable")
        tab.cmd("Page.reload", {"ignoreCache": False})
        tab.ws.settimeout(30)
        deadline = time.time() + 30
        while time.time() < deadline:
            m = json.loads(tab.ws.recv())
            if m.get("method") == "Page.loadEventFired":
                break
    finally:
        tab.close()


if __name__ == "__main__":
    main()
