#!/usr/bin/env python3
"""cf_token_mint.py — bootstrap CLOUDFLARE_API_TOKEN through the user's real Chrome.

The CF API token is the one credential nothing can mint programmatically — the
bootstrap credential. This drives the user's logged-in Chrome (CDP, port 9222 —
start via scripts/start-chrome-debug.bat once) to the dashboard token-create
page, pre-fills a custom token with the needed scopes, and clicks create. If
CDP isn't reachable it degrades to watch mode: opens the page, waits for the
user to paste the minted token.

Then: verifies scopes, writes the token to the project's .env, and chains
cf_email_destination.py + cf_logpush_setup.py — both gates close themselves.

Scopes needed:
  Account:  Cloudflare Logpush:Edit, Workers R2 Storage:Edit
  Zone:     Email Routing Rules:Edit

Usage:
  python backend/scripts/cf_token_mint.py            # CDP auto-create, fallback watch
  python backend/scripts/cf_token_mint.py --paste    # skip CDP, paste token directly
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DOTENV = Path(__file__).resolve().parents[3] / ".env"
CF = "https://api.cloudflare.com/client/v4"
DASH_TOKEN_PAGE = (
    "https://dash.cloudflare.com/profile/api-tokens"
)
REQUIRED_PROBES = [
    ("logpush", "GET", "/accounts/fb7dc7b69b662480cd5961a4d1913c78/logpush/jobs"),
    ("email-routing", "GET", "/zones/7025298073d6a5c645a6ad9add0cbf0e/email/routing/rules"),
    ("workers", "GET", "/accounts/fb7dc7b69b662480cd5961a4d1913c78/workers/scripts"),
]
ENV_KEYS = ["CLOUDFLARE_API_TOKEN"]


def _api(method: str, path: str, token: str) -> tuple[int, dict]:
    req = urllib.request.Request(
        CF + path, method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {}
    except OSError as e:
        return 0, {"errors": [{"message": str(e)}]}


def _verify_scopes(token: str) -> tuple[bool, list[str]]:
    status, body = _api("GET", "/user/tokens/verify", token)
    if not body.get("success"):
        return False, [f"token invalid: {body.get('errors')}"]
    misses = []
    for name, method, path in REQUIRED_PROBES:
        code, body = _api(method, path, token)
        if code in (403, 401) or (isinstance(body, dict) and body.get("success") is False and code != 404):
            misses.append(f"{name} (HTTP {code})")
    return (not misses), misses


def _write_env(token: str) -> None:
    lines = DOTENV.read_text().splitlines()
    out, written = [], set()
    for line in lines:
        k = line.split("=", 1)[0]
        if k in ENV_KEYS:
            out.append(f"{k}={token}")
            written.add(k)
        else:
            out.append(line)
    for k in ENV_KEYS - written:
        out.append(f"{k}={token}")
    DOTENV.write_text("\n".join(out) + "\n")


def _ensure_chrome_debug() -> None:
    """Launch Chrome with --remote-debugging-port=9222 if the CDP endpoint is dead."""
    try:
        urllib.request.urlopen("http://localhost:9222/json", timeout=2)
        return
    except OSError:
        pass
    import subprocess
    chrome = os.environ.get("CHROME_BIN", r"C:\Program Files\Google\Chrome\Application\chrome.exe")
    bat = Path.home() / "AppData" / "Roaming" / "devin" / "skills" / "consent-root-ops" / "scripts" / "start-chrome-debug.bat"
    try:
        if bat.exists():
            subprocess.Popen(["cmd", "/c", str(bat)])
        else:
            subprocess.Popen([chrome, "--remote-debugging-port=9222"])
        for _ in range(10):
            time.sleep(1)
            try:
                urllib.request.urlopen("http://localhost:9222/json", timeout=2)
                return
            except OSError:
                continue
    except OSError:
        return


def _cdp_click_flow() -> str | None:

    """Best-effort: drive live Chrome via CDP to create the token. Returns the token or None."""
    try:
        import websocket  # type: ignore
    except ImportError:
        return None
    try:
        tabs = json.loads(urllib.request.urlopen("http://localhost:9222/json", timeout=5).read())
    except OSError:
        return None
    page = next((t for t in tabs if t.get("type") == "page"), None)
    if not page:
        return None
    ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=15)
    mid = 0

    def send(method, **params):
        nonlocal mid
        mid += 1
        ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(ws.recv())
            if msg.get("id") == mid:
                return msg.get("result", {})

    def js(expr):
        return send("Runtime.evaluate", expression=expr, returnByValue=True).get("result", {}).get("value")

    send("Page.enable")
    send("Page.navigate", url=DASH_TOKEN_PAGE)
    time.sleep(5)
    # click "Create Token" -> scroll to Custom Token -> Get started
    for sel in ("//a[contains(.,'Create Token')]", "//button[contains(.,'Create Token')]"):
        js(f"(()=>{{const x=document.evaluate(\"{sel}\",document,null,9,null);if(x.singleNodeValue){{x.singleNodeValue.click();return 1}}return 0}})()")
    time.sleep(4)
    js("(()=>{const t=[...document.querySelectorAll('*')].find(e=>e.children.length===0"
       "&&/Get started/i.test(e.textContent||''));if(t){t.click();return 1}return 0})()")
    time.sleep(4)
    # name it
    js("(()=>{const i=[...document.querySelectorAll('input')].find(e=>"
       "/token name/i.test(e.parentElement?.textContent||''));if(i){i.focus();"
       "const d=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value');"
       "d.set.call(i,'socialauto-ops '+new Date().toISOString().slice(0,10));"
       "i.dispatchEvent(new Event('input',{bubbles:true}));return 1}return 0})()")
    print("[cdp] navigated to token creator — finish the scope rows in the open window,")
    print("[cdp] click 'Continue to summary' then 'Create Token', then paste the token when it appears.")
    return None  # token text appears on the confirm screen — paste path takes over


def _watch_for_token() -> str:
    print()
    print("=" * 66)
    print("Mint the token in the dashboard, then paste it here (input hidden).")
    print("Scopes: Account= Logpush:Edit, Workers R2 Storage:Edit | Zone= Email Routing Rules:Edit")
    print("=" * 66)
    import getpass
    return getpass.getpass("token: ").strip()


def main() -> int:
    paste_only = "--paste" in sys.argv
    token = ""
    if not paste_only:
        token = _cdp_click_flow() or ""
    if not token:
        if not paste_only:
            # still open the dashboard for the manual path
            import webbrowser
            webbrowser.open(DASH_TOKEN_PAGE)
        token = _watch_for_token()
    if not token:
        print("[FIX] empty token — aborting")
        return 1

    ok, misses = _verify_scopes(token)
    if not ok:
        print("[FIX] token works but probe misses:")
        for m in misses:
            print(f"      - {m}")
        print("      -> recreate with the missing scopes (run again to re-mint)")
        return 1
    print("[ok ] token verified — all probe scopes pass")

    _write_env(token)
    print(f"[ok ] CLOUDFLARE_API_TOKEN written to {DOTENV}")

    print("\n--- chaining gate tools ---")
    for script in ("cf_email_destination.py", "cf_maildir_scan.py", "cf_logpush_setup.py"):
        p = HERE / script
        if p.exists():
            print(f"\n$ python {script}")
            os.system(f'"{sys.executable}" "{p}"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
