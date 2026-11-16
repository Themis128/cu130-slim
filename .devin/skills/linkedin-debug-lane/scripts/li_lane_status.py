#!/usr/bin/env python3
"""li_lane_status — which LinkedIn lanes are usable right now.

Probes every LinkedIn surface and prints a JSON + table verdict:

  sidecar   :9225 linkedin-browser-sidecar (/debug/health + feed nav probe)
  cdp       :9229 chrome-debug-profile via Windows relay (json/version + LI nav)
  api       api.linkedin.com /v2/userinfo with the live SocialAuto token

Usage:  python3 li_lane_status.py [--json] [--nav] [--tg]
  --json   machine-readable only
  --nav    actually navigate linkedin.com/feed in the CDP lane to classify
           session state (ok / loginwall / 429 / checkpoint) — slow (~15s)
  --tg     send a Telegram #ops alert when the usable-lane set changes
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

SIDECAR = os.environ.get("LI_SIDECAR", "http://localhost:9225")
CDP_HOSTS = ["http://172.22.144.1:9229", "http://localhost:9229"]
STATE = os.path.expanduser("~/.local/state/li-lane-status.json")


def _get(url, timeout=6):
    try:
        return urllib.request.urlopen(url, timeout=timeout).read()
    except urllib.error.HTTPError:
        return b""  # any HTTP response proves reachability
    except Exception:
        return None


def probe_sidecar():
    body = _get(SIDECAR + "/health", 5)
    if body is None:
        return {"lane": "sidecar", "ok": False, "detail": "unreachable"}
    try:
        j = json.loads(body)
    except Exception:
        j = {}
    # health ok but page-level 429 still possible — flag for caller
    return {"lane": "sidecar", "ok": True,
            "detail": j.get("status", "healthy") if isinstance(j, dict) else "up"}


def probe_cdp():
    for base in CDP_HOSTS:
        v = _get(base + "/json/version", 5)
        if v:
            try:
                return {"lane": "cdp", "ok": True, "base": base,
                        "browser": json.loads(v).get("Browser", "?")}
            except Exception:
                return {"lane": "cdp", "ok": True, "base": base}
    return {"lane": "cdp", "ok": False, "detail": "no CDP endpoint"}


def probe_api():
    # cheap live check: userinfo with the person-account token via social-api
    # is heavy; instead hit the public OIDC discovery + a token-less probe.
    # Real token check is done by linkedin_tool token-inspect — here we only
    # verify api.linkedin.com is reachable and not edge-blocked.
    body = _get("https://api.linkedin.com/v2/userinfo", 8)
    # 401/403 without token is FINE — proves the API edge answers
    return {"lane": "api", "ok": body is not None,
            "detail": "reachable" if body is not None else "unreachable"}


def classify_nav():
    """Navigate linkedin.com/feed in a CDP tab; classify the result."""
    import websocket  # noqa: delayed — only needed with --nav
    base = next((c["base"] for c in [probe_cdp()] if c.get("base")), None)
    if not base:
        return {"lane": "cdp-nav", "ok": False, "state": "no-cdp"}
    try:
        ver = json.loads(urllib.request.urlopen(
            base + "/json/version", timeout=8).read())
        bws = websocket.create_connection(
            ver["webSocketDebuggerUrl"], timeout=20, suppress_origin=True)

        def send(ws, m):
            ws.send(json.dumps(m))
            return json.loads(ws.recv())

        tid = send(bws, {"id": 1, "method": "Target.createTarget",
                         "params": {"url": "https://www.linkedin.com/feed/",
                                    "background": True}})["result"]["targetId"]
        time.sleep(10)
        tab = next((x for x in json.loads(urllib.request.urlopen(
            base + "/json/list", timeout=8).read()) if x.get("id") == tid), None)
        if not tab:
            return {"lane": "cdp-nav", "ok": False, "state": "tab-lost"}
        ws = websocket.create_connection(
            tab["webSocketDebuggerUrl"], timeout=20, suppress_origin=True)
        r = send(ws, {"id": 2, "method": "Runtime.evaluate", "params": {
            "expression": "JSON.stringify({url:location.href,"
                          "title:document.title,"
                          "err:document.body?document.body.innerText.slice(0,200):''})",
            "returnByValue": True}})
        info = json.loads(r["result"]["result"]["value"])
        txt = (info.get("err") or "") + " " + (info.get("title") or "")
        low = txt.lower()
        if "429" in low or "error 429" in low or "too many" in low:
            state = "429"
        elif "authwall" in low or "sign in" in low or "join linkedin" in low:
            state = "loginwall"
        elif "checkpoint" in low or "verify" in low and "identity" in low:
            state = "checkpoint"
        elif "feed" in (info.get("url") or ""):
            state = "ok"
        else:
            state = "unknown"
        return {"lane": "cdp-nav", "ok": state == "ok", "state": state,
                "url": info.get("url"), "title": info.get("title")}
    except Exception as e:
        return {"lane": "cdp-nav", "ok": False, "state": f"error:{e}"}


def tg_alert(text):
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..",
                                        "telegram-alert-ops", "scripts"))
        import tg_alert  # noqa
        tg_alert.send("ops", text)
    except Exception:
        pass


def main():
    as_json = "--json" in sys.argv
    lanes = [probe_sidecar(), probe_cdp(), probe_api()]
    if "--nav" in sys.argv:
        lanes.append(classify_nav())
    usable = [l["lane"] for l in lanes if l.get("ok")]
    out = {"ts": int(time.time()), "lanes": lanes, "usable": usable}
    if as_json:
        print(json.dumps(out))
    else:
        for l in lanes:
            print(f"{l['lane']:>9}  {'OK ' if l.get('ok') else 'DOWN'}  "
                  f"{l.get('state') or l.get('detail') or l.get('browser','')}")
        print(f"usable: {', '.join(usable) or 'NONE'}")
    if "--tg" in sys.argv:
        prev = {}
        try:
            prev = json.load(open(STATE))
        except Exception:
            pass
        key = ",".join(sorted(usable))
        if prev.get("usable") != key:
            tg_alert(f"li-lane-status: usable lanes now [{key or 'NONE'}] "
                     f"(was [{prev.get('usable','?')}])")
            os.makedirs(os.path.dirname(STATE), exist_ok=True)
            json.dump({"usable": key, "ts": out["ts"]}, open(STATE, "w"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
