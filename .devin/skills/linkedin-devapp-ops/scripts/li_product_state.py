"""Per-app product state table — the authoritative "what still needs action".

Usage: python li_product_state.py <app_id> [<app_id>…]
Env: CDP_URL (default http://127.0.0.1:9229).

Dumps every product card's state: added / Access request form /
Review in progress / Request access / Access denied.
"""

import json
import sys
import time
import urllib.request

import websocket

CDP = __import__("os").environ.get("CDP_URL", "http://127.0.0.1:9229")


def send(ws, m):
    ws.send(json.dumps(m))
    return json.loads(ws.recv())


def ev(ws, expr, t=20):
    r = send(ws, {"id": 1, "method": "Runtime.evaluate", "params": {"expression": expr, "returnByValue": True}})
    return r.get("result", {}).get("result", {}).get("value")


def tabs():
    return json.loads(urllib.request.urlopen(CDP + "/json/list", timeout=10).read())


def state_for(app_id):
    ver = json.loads(urllib.request.urlopen(CDP + "/json/version", timeout=10).read())
    bws = websocket.create_connection(ver["webSocketDebuggerUrl"], timeout=20, suppress_origin=True)
    send(bws, {"id": 1, "method": "Target.createTarget",
               "params": {"url": f"https://www.linkedin.com/developers/apps/{app_id}/products", "background": True}})
    tid = json.loads(json.dumps(send(bws, {"id": 2, "method": "Target.getTargets"})))
    time.sleep(14)
    tab = next(x for x in tabs() if x.get("type") == "page" and f"/apps/{app_id}/" in x.get("url", ""))
    ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=20, suppress_origin=True)
    out = ev(ws, """JSON.stringify([...document.querySelectorAll('[class*=product],[class*=card]')]
      .map(c=>(c.innerText||'').replace(/\\s+/g,' ').trim())
      .filter(t=>t.length>20&&t.length<500))""") or "[]"
    ws.close()
    return json.loads(out)


def classify(txt):
    t = txt.lower()
    if "access denied" in t:
        return "DENIED"
    if "review in progress" in t or "review" in t:
        return "REVIEW"
    if "access request form" in t:
        return "FORM"
    if "request access" in t:
        return "REQ"
    return "added"


def main():
    for app in sys.argv[1:] or ["265077368", "264925843", "265077464", "265081467", "227354605"]:
        print(f"### {app}")
        try:
            seen = set()
            for card in state_for(app):
                # one line per product — the card text repeats in nested containers
                head = card.split("  ")[0][:40]
                if head in seen:
                    continue
                seen.add(head)
                print(f"   {head:42} {classify(card)}")
        except Exception as e:
            print("   err", str(e)[:80])


if __name__ == "__main__":
    main()
