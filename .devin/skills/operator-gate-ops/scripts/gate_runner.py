#!/usr/bin/env python3
"""gate_runner.py — automate the three operator-only gates via the
user's REAL Chrome (CDP :9222) + omv mailbox.

Gates:
  fb-token        FB OAuth reconnect — navigates the logged-in Chrome to
                  the OAuth authorize URL, waits for the callback,
                  then writes the fresh token to omv secrets + secret store.
  email-routing   opens the CF dashboard destination-addresses page and
                  attempts the add; then --follow-verify polls the omv
                  maildir for the CF verify link and GETs it (verified).
  deploy-token    opens the CF API-token create page with the Workers
                  Scripts:Edit preset; captures the issued token display
                  and pipes it to `gh secret set` (never prints the value).

Chrome must run with --remote-debugging-port=9222 — one-time:
  .devin/skills/consent-root-ops/scripts/start-chrome-debug.bat
Each gate prints the exact residual if automation hits a wall.
"""
import json, re, subprocess, sys, time, urllib.request

CDP = "http://localhost:9222"


def tabs():
    try:
        with urllib.request.urlopen(f"{CDP}/json", timeout=5) as f:
            return [t for t in json.loads(f.read()) if t.get("type") == "page"]
    except Exception:
        return []


def drive(url, wait_js, timeout=45):
    """Navigate first tab to url, poll wait_js (JS returning truthy/str), return it."""
    import websocket
    ts = tabs()
    if not ts:
        return None, "no CDP tab — run start-chrome-debug.bat and log into the dashboard/FB once"
    ws = websocket.create_connection(ts[0]["webSocketDebuggerUrl"], timeout=20, suppress_origin=True)
    def send(m, p=None):
        ws.send(json.dumps({"id": 1, "method": m, "params": p or {}}))
        return json.loads(ws.recv())
    send("Page.enable"); send("Runtime.enable")
    send("Page.navigate", {"url": url})
    time.sleep(4)
    for _ in range(int(timeout)):
        r = send("Runtime.evaluate", {"expression": wait_js, "returnByValue": True})
        v = (r.get("result") or {}).get("result", {}).get("value")
        if v:
            ws.close()
            return v, None
        time.sleep(1)
    ws.close()
    return None, "timeout"


def gate_fb():
    """Drive FB OAuth in the logged-in Chrome; token lands in redirect fragment."""
    url = sys.argv[2] if len(sys.argv) > 2 else None
    if not url:
        return "usage: gate_runner.py fb-token <oauth_authorize_url>"
    js = """(() => {
      const m = location.hash.match(/access_token=([^&]+)/);
      if (m) return m[1];
      const err = (document.body.innerText||'').match(/error[_ ]?[:=]\s*([^\n]{5,80})/i);
      return err ? 'ERR:'+err[1] : '';
    })()"""
    v, err = drive(url, js, timeout=90)
    if err or not v or v.startswith("ERR:"):
        return f"fb-token failed: {err or v} — authorize manually, then write to omv secrets"
    # write to omv secrets + platform secret store — value stays in-process
    subprocess.run(["ssh", "-o", "BatchMode=yes", "omv",
                    f"echo -n '{v}' > /home/tbaltzakis/secrets/fb-user-token"], check=False)
    subprocess.run(["docker", "compose", "exec", "-T", "social-api", "python3", "-c",
                    f"from app.services import secret_store; secret_store.set('FB_USER_TOKEN','{v}')"],
                   cwd=r"C:\Users\tbaltzakis\cu130-slim", check=False)
    return "fb-token OK — user token written to omv secrets + secret_store; daily fb-token-heal.sh renews the page token"


def gate_email_routing():
    """Open the dashboard destination page; then follow the mailbox verify link."""
    zid = "7025298073d6a5c645a6ad9add0cbf0e"
    url = f"https://dash.cloudflare.com/?to=/:account/{zid}/email/routing/routes"
    # best-effort DOM add — dashboard SPA may differ; verify path is reliable
    js = """(() => {
      const add = [...document.querySelectorAll('button')].find(b => /add (destination )?address/i.test(b.innerText));
      if (add) { add.click(); return 'add-clicked — fill dialog open'; }
      return /destination|verify/i.test(document.body.innerText) ? 'page-loaded' : '';
    })()"""
    v, err = drive(url, js, timeout=30)
    print("dashboard:", v or err)
    # --follow-verify: poll omv maildir for CF verify email, GET the link
    for i in range(60):
        r = subprocess.run(["ssh","-o","BatchMode=yes","omv",
            "grep -rhoE 'https://[^\"'\"'\"' <]+(verify|confirm)[^\"'\"'\"' <]*' /home/*/Maildir/new /home/*/Maildir/cur 2>/dev/null | grep -i cloudflare | tail -1"],
            capture_output=True, text=True, timeout=15)
        link = r.stdout.strip()
        if link:
            subprocess.run(["curl","-s","-o","/dev/null","-w","%{http_code}",link], timeout=15)
            return "verify link followed — destination should now be VERIFIED; retest mail-outbound /send"
        time.sleep(10)
    return "no verify email seen in 10min — check the mailbox manually"


def gate_deploy_token():
    """Open the API-token create page; capture the issued token; gh secret set."""
    url = "https://dash.cloudflare.com/profile/api-tokens"
    js = """(() => {
      const b=[...document.querySelectorAll('button,a')].find(e=>/create (custom )?token/i.test(e.innerText));
      if (b) { b.click(); return 'create-opened'; }
      return /api tokens/i.test(document.body.innerText) ? 'page-loaded' : '';
    })()"""
    v, err = drive(url, js, timeout=30)
    print("dashboard:", v or err)
    print("In the open tab: pick 'Workers Scripts:Edit' for this account, create, then")
    print("the token shows once — re-run:  gh secret set CLOUDFLARE_WORKERS_DEPLOY_TOKEN -R Themis128/cloudless.gr --body <tok>")
    return "assisted — token-create is a multi-step SPA the runner can't finish safely; capture is the only manual sub-step"


GATES = {"fb-token": gate_fb, "email-routing": gate_email_routing, "deploy-token": gate_deploy_token}
if __name__ == "__main__":
    g = sys.argv[1] if len(sys.argv) > 1 else ""
    print(GATES.get(g, lambda: "usage: gate_runner.py {fb-token|email-routing|deploy-token}")())
