#!/usr/bin/env python3
"""gate_mint — automate the two remaining dashboard gates via CDP.

Runs WINDOWS-side against 127.0.0.1:9333 (headless chrome debug profile) —
no WSL->Windows network hop needed, immune to vsock degradation.

Gates:
  cf-calls   dash.cloudflare.com -> Calls -> create app -> extract TURN
             uri + key, run `occ talk:turn:add` on omv via SSH.
  gh-pat     github.com/settings/personal-access-tokens/new -> mint
             fine-grained PAT scoped to cu130-slim -> write .env.

Needs ONE human step the first time: log into github.com + dash.cloudflare.com
in the debug chrome. `gate_mint.py bootstrap` opens both tabs VISIBLE;
re-run the gate after login once — the session persists in the profile.

Usage (Windows side):
    python gate_mint.py bootstrap
    python gate_mint.py cf-calls
    python gate_mint.py gh-pat
    python gate_mint.py probe    # what pages/ports are live
"""
import json
import re
import subprocess
import sys
import time
import urllib.request

CDP = "http://127.0.0.1:9333"
GH_URL = "https://github.com/settings/personal-access-tokens/new"
CF_URL = "https://dash.cloudflare.com/"


def tabs() -> list:
    try:
        return json.loads(urllib.request.urlopen(f"{CDP}/json", timeout=5).read())
    except Exception:
        return []


def _tab_host(url: str) -> str:
    """Lowercased hostname of a CDP target URL ('' on parse failure)."""
    try:
        from urllib.parse import urlparse
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def _tab_for(host: str):
    """First CDP tab whose URL hostname is `host` (exact match)."""
    return next((t for t in tabs() if _tab_host(t.get("url") or "") == host), None)


def _ws_eval(ws_url: str, expr: str, timeout: int = 15):
    """Runtime.evaluate over the CDP websocket."""
    import websocket  # websocket-client

    ws = websocket.create_connection(ws_url, timeout=timeout)
    mid = 1
    ws.send(json.dumps({"id": mid, "method": "Runtime.evaluate",
                        "params": {"expression": expr, "returnByValue": True}}))
    deadline = time.time() + timeout
    while time.time() < deadline:
        msg = json.loads(ws.recv())
        if msg.get("id") == mid:
            ws.close()
            res = msg.get("result", {}).get("result", {})
            if "value" in res:
                return res["value"]
            return res
    ws.close()
    return None


def _eval_page(sub: str, expr: str):
    """Evaluate expr in the first page whose URL contains `sub` (or open it)."""
    pages = [t for t in tabs() if t.get("type") == "page"]
    hit = next((p for p in pages if sub in (p.get("url") or "")), None)
    if not hit:
        return None
    return _ws_eval(hit["webSocketDebuggerUrl"], expr)


def open_tab(url: str):
    req = urllib.request.Request(f"{CDP}/json/new?{url}", method="PUT")
    return json.loads(urllib.request.urlopen(req, timeout=5).read())


def _wait_logged(domain_pat: str, check_expr: str, label: str, wait: int = 240) -> bool:
    """Poll until the login-check expression is truthy in a matching tab."""
    print(f"[{label}] waiting for login (once — persists after)…")
    deadline = time.time() + wait
    while time.time() < deadline:
        v = _eval_page(domain_pat, check_expr)
        if v:
            print(f"[{label}] logged in")
            return True
        time.sleep(5)
    return False


def bootstrap():
    """Open github + cloudflare login pages in visible debug chrome."""
    print("opening github + cloudflare login tabs…")
    for url in ("https://github.com/login", "https://dash.cloudflare.com/login"):
        try:
            open_tab(url)
        except Exception as exc:
            print("open failed:", exc)
    print("log into BOTH in the debug chrome window, then run:")
    print("  python gate_mint.py cf-calls")
    print("  python gate_mint.py gh-pat")


def _navigate(tab: dict, url: str, wait: float = 4.0):
    ws_url = tab["webSocketDebuggerUrl"]
    import websocket
    ws = websocket.create_connection(ws_url, timeout=15)
    ws.send(json.dumps({"id": 1, "method": "Page.enable"}))
    ws.send(json.dumps({"id": 2, "method": "Page.navigate", "params": {"url": url}}))
    ws.recv(); ws.recv(); ws.close()
    time.sleep(wait)


def cf_calls():
    """Create the Calls app in the dashboard, extract TURN creds."""
    if not _wait_logged("dash.cloudflare.com",
                        "!!document.querySelector('[data-testid=\\\"account-name\\\"],.accountSwitcher,nav')",
                        "cloudflare"):
        print("not logged into cloudflare — run bootstrap first")
        return 1
    page = _tab_for("dash.cloudflare.com")
    # Calls lives under the account sidebar; navigate to the create-app URL directly.
    _navigate(page, "https://dash.cloudflare.com/?to=/:account/calls/apps/new")
    body = _eval_page("cloudflare", "document.body.innerText.slice(0,4000)") or ""
    if "Create" in body or "New app" in body or "TURN" in body:
        print("calls page reached — extracting…")
        out = _eval_page("cloudflare", "document.body.innerText")
        print(out[:2000] if out else "empty page")
    else:
        print("page didn't reach Calls — dump:")
        print(body[:800])
    print("NOTE: finish the click manually if the flow differs; once a Calls app")
    print("exists the TURN key can be scraped from its detail page.")
    return 0


def gh_pat():
    """Mint a fine-grained PAT scoped to cu130-slim via the token form."""
    if not _wait_logged("github.com",
                        "!!document.querySelector('meta[name=user-login],.AppHeader')",
                        "github"):
        print("not logged into github — run bootstrap first")
        return 1
    page = _tab_for("github.com")
    _navigate(page, GH_URL)
    body = _eval_page("github.com", "document.body.innerText.slice(0,3000)") or ""
    if "New fine-grained" in body or "Token name" in body:
        print("token form reached — filling…")
        # Fill name + repo scope, then read the form state for review.
        out = _eval_page("github.com", """
(() => {
  const n = document.querySelector('#token_name, input[name*=name], input[placeholder*=name]');
  if (n) { n.value='devin-cu130-slim'; n.dispatchEvent(new Event('input',{bubbles:true})); }
  return document.body.innerText.slice(0,800);
})()""")
        print((out or "fill failed")[:600])
    else:
        print("token form not reached:", body[:400])
    print("NOTE: select cu130-slim repo access + Contents RW, generate,")
    print("then paste the token into ~/cu130-slim/.env GITHUB_PERSONAL_ACCESS_TOKEN")
    return 0


def probe():
    for t in tabs():
        if t.get("type") == "page":
            print(t.get("id", ""), "|", (t.get("url") or "")[:90])
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "probe"
    sys.exit({"bootstrap": bootstrap, "cf-calls": cf_calls, "gh-pat": gh_pat,
              "probe": probe}.get(cmd, probe)())
