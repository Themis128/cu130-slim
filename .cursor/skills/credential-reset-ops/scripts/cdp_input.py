#!/usr/bin/env python3
"""cdp_input.py — trusted-input injection over Chrome DevTools Protocol.

Unlike JS `input.value = x` (synthetic, bots-detectable), CDP
`Input.insertText` + `Input.dispatchMouseEvent` produce REAL trusted
input events — the same trust level as a human typing/clicking. This is
what passes Microsoft's (and Meta's) trusted-input gates on credential
forms.

Usage (library):
  from cdp_input import CDP
  c = CDP()                      # localhost:9222
  c.eval("location.href=...")
  c.insert_text("user@x.com")    # trusted text into focused field
  c.click_xy(540, 800)           # trusted click

Requires `websocket-client` (pip install websocket-client) or falls
back to the /json/list + ws via stdlib socket hack is NOT provided —
websocket-client is required.
"""
import json
import urllib.request


class CDP:
    def __init__(self, port=9222, page_index=0):
        import websocket  # websocket-client
        tabs = json.loads(urllib.request.urlopen(
            f"http://localhost:{port}/json/list", timeout=8).read())
        pages = [t for t in tabs if t.get("type") == "page"]
        self.ws = websocket.create_connection(
            pages[page_index]["webSocketDebuggerUrl"], timeout=30)
        self.mid = 0

    def _call(self, method, **params):
        self.mid += 1
        self.ws.send(json.dumps({"id": self.mid, "method": method,
                                 "params": params}))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == self.mid:
                return m.get("result", {})

    def eval(self, expr):
        r = self._call("Runtime.evaluate", expression=expr,
                       returnByValue=True, awaitPromise=True)
        return r.get("result", {}).get("value")

    def nav(self, url):
        self._call("Page.navigate", url=url)

    def focus_css(self, sel):
        """Focus an element via JS, then inputs land as trusted."""
        return self.eval(
            f"(()=>{{const i=document.querySelector({json.dumps(sel)});"
            "if(i){i.focus();return 'focused'}return 'miss'}})()")

    def insert_text(self, text):
        """Trusted text entry — types into the focused element."""
        self._call("Input.insertText", text=text)

    def click_xy(self, x, y):
        for t in ("mousePressed", "mouseReleased"):
            self._call("Input.dispatchMouseEvent", type=t, x=x, y=y,
                       button="left", clickCount=1)

    def click_text(self, text):
        """Find element by text, click its center via trusted mouse event."""
        r = self.eval(
            "(()=>{const els=[...document.querySelectorAll('button,a,"
            "input[type=submit],div[role=button],span')];"
            f"const b=els.find(e=>((e.innerText||e.value||'').trim().toLowerCase()==='{text.lower()}'))"
            ";if(!b)return 'miss';const r=b.getBoundingClientRect();"
            "return JSON.stringify({x:r.x+r.width/2,y:r.y+r.height/2})})()")
        if r in (None, "miss"):
            return "miss"
        p = json.loads(r)
        self.click_xy(p["x"], p["y"])
        return "clicked"


if __name__ == "__main__":
    c = CDP()
    print(c.eval("location.href"))
