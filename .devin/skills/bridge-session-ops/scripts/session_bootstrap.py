#!/usr/bin/env python3
"""session_bootstrap.py — automated login bootstrap for platforms with
stored credentials in social_secrets. Turns "log in via noVNC" into a
scripted form-fill via the browser bridge's /session/evaluate.

  python3 session_bootstrap.py <platform> [--bridge http://localhost:9223]

Supported: instagram (INSTAGRAM_USERNAME/PASSWORD). X/FB have no stored
passwords — they stay physical (reports that explicitly).
"""
from __future__ import annotations
import json
import sys
import time
import urllib.request
import urllib.error
import urllib.parse

BRIDGE = "http://localhost:9223"

def ev(expr: str, bridge: str = BRIDGE, tries: int = 6) -> dict:
    last = None
    for _ in range(tries):
        try:
            r = urllib.request.Request(bridge + "/session/evaluate",
                data=json.dumps({"expression": expr}).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(r, timeout=20) as f:
                return json.loads(f.read())
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 409:
                time.sleep(20)
                continue
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}
    return {"error": f"bridge busy: {last}"}

def nav(url: str, bridge: str = BRIDGE):
    ev(f"location.href={json.dumps(url)}", bridge)
    time.sleep(5)

def creds(platform: str) -> dict:
    import subprocess
    keys = {"instagram": ["INSTAGRAM_USERNAME", "INSTAGRAM_PASSWORD"]}
    out = {}
    for k in keys.get(platform, []):
        r = subprocess.run(
            ["docker", "compose", "exec", "-T", "social-postgres", "psql",
             "-U", "social_user", "-d", "social_automation", "-t", "-c",
             f"SELECT value FROM social_secrets WHERE key='{k}'"],
            capture_output=True, text=True)
        v = r.stdout.strip()
        if v:
            out[k.lower()] = v
    return out

def ig_login(bridge: str) -> str:
    c = creds("instagram")
    if not (c.get("instagram_username") and c.get("instagram_password")):
        return "no-creds — physical login needed"
    nav("https://www.instagram.com/accounts/login/", bridge)
    time.sleep(4)
    js = ("(()=>{const u=%s;const p=%s;const s=Object.getOwnPropertyDescriptor("
          "window.HTMLInputElement.prototype,'value').set;const f=(e,v)=>{s.call(e,v);"
          "e.dispatchEvent(new Event('input',{bubbles:true}))};"
          "const uf=document.querySelector('input[name=email]');"
          "const pf=document.querySelector('input[name=pass]');"
          "if(!uf||!pf)return 'form-not-found';"
          "f(uf,u);f(pf,p);"
          "pf.closest('form').querySelector('button[type=submit]').click();"
          "return 'submitted'})()"
          ) % (json.dumps(c['instagram_username']), json.dumps(c['instagram_password']))
    r = ev(js, bridge)
    if r.get("error") or "submitted" not in str(r.get("result", "")):
        return f"fill-failed: {r}"
    time.sleep(8)
    loc = ev("location.href", bridge).get("result", "")
    if "login" in loc or "challenge" in loc:
        return f"login-wall-persists ({loc[:60]}) — 2FA/checkpoint needs human"
    host = urllib.parse.urlparse(loc).hostname or ""
    if host == "instagram.com" or host.endswith(".instagram.com"):
        return f"logged-in ({loc[:60]})"
    return f"unclear ({loc[:80]})"

def main():
    platform = sys.argv[1] if len(sys.argv) > 1 else ""
    bridge = next((a.split("=",1)[1] for a in sys.argv if a.startswith("--bridge")), BRIDGE)
    if platform == "instagram":
        print(ig_login(bridge))
        return 0
    if platform in ("x", "facebook"):
        print(f"{platform}: no stored password — physical login via noVNC")
        return 1
    print(__doc__)
    return 1

if __name__ == "__main__":
    sys.exit(main())
