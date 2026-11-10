#!/usr/bin/env python3
"""x_finalize.py — Windows-side post-OAuth finalizer for the X recovery daemon.

Runs after x_daemon reports phase=done (OAuth callback reached). Extracts the
fresh X session cookies (auth_token, ct0) from the probe Chrome via CDP and
writes them to x_cookies.json for the WSL-side finisher to store.

Usage: python x_finalize.py [--wait]   (--wait polls status until done, max 6h)
"""
import json, sys, time, os, urllib.request
import websocket

PORT = "9444"
ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
if ARGS:
    PORT = ARGS[0]
CDP = f"http://127.0.0.1:{PORT}"
DIR = r"C:\Users\tbaltzakis"
SUF = "" if PORT == "9444" else f"_{PORT}"
STATUS = os.path.join(DIR, f"x_daemon_status{SUF}.json")
OUT = os.path.join(DIR, f"x_cookies{SUF}.json")
FIN_STATUS = os.path.join(DIR, f"x_finalize_status{SUF}.json")

def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)

def set_status(phase, detail=""):
    json.dump({"phase": phase, "detail": detail, "ts": time.time()}, open(FIN_STATUS, "w"))

def browser_ws():
    v = json.loads(urllib.request.urlopen(CDP + "/json/version", timeout=8).read())
    return websocket.create_connection(v["webSocketDebuggerUrl"], timeout=25, suppress_origin=True)

def get_x_cookies():
    ws = browser_ws()
    ws.send(json.dumps({"id": 1, "method": "Storage.getCookies", "params": {}}))
    cookies = []
    while True:
        m = json.loads(ws.recv())
        if m.get("id") == 1:
            cookies = m.get("result", {}).get("cookies", [])
            break
    ws.close()
    return {c["name"]: c["value"] for c in cookies if "x.com" in c.get("domain", "") or "twitter.com" in c.get("domain", "")}

def main():
    wait = "--wait" in sys.argv
    # when polling, look at this port's daemon status; also accept a shared
    # unsuffixed status for the primary daemon
    deadline = time.time() + 6 * 3600
    while True:
        try:
            st = json.load(open(STATUS))
        except Exception:
            st = {}
        if st.get("phase") == "done":
            break
        if PORT != "9444":
            try:
                st2 = json.load(open(os.path.join(DIR, "x_daemon_status.json")))
                if st2.get("phase") == "done":
                    break
            except Exception:
                pass
        if not wait or time.time() > deadline:
            set_status("no_done", str(st.get("phase")))
            log("daemon not done:", st.get("phase")); return
        time.sleep(60)
    set_status("extracting", "pulling cookies")
    for attempt in range(6):
        try:
            ck = get_x_cookies()
            if ck.get("auth_token") and ck.get("ct0"):
                json.dump(ck, open(OUT, "w"))
                set_status("done", f"cookies extracted: {len(ck)} x.com cookies")
                log("cookies written:", sorted(ck.keys()))
                return
            log("attempt", attempt, "missing auth_token/ct0 — keys:", sorted(ck.keys()))
        except Exception as e:
            log("attempt", attempt, "err:", e)
        time.sleep(15)
    set_status("failed", "no auth_token/ct0 after retries")

if __name__ == "__main__":
    main()
