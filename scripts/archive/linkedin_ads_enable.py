#!/usr/bin/env python3
"""One-shot LinkedIn Advertising Reporting activation pipeline.

Stages (idempotent, safe to re-run):
  A. Claim the browser-novnc bridge for linkedin (polite retries only —
     this is a non-urgent scheduled task; never force-preempts).
  B. Developer console: enable the "Advertising Reporting API" product on
     the SocialAuto app (self-serve, dev tier covers <=5 ad accounts).
  C. Flip .env: uncomment LINKEDIN_EXTRA_SCOPES=r_ads_reporting, recreate
     social-api so the scope lands in future OAuth connects.
  D. Re-drive the LinkedIn OAuth connect via the bridge consent flow so the
     stored token gains r_ads_reporting.
  E. Verify account scopes + write the done-flag.

Run via cron tomorrow (Sep 25): only fires that day; writes
/tmp/linkedin_ads_enable.done on success so later runs no-op.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

REPO = "/home/tbaltzakis/cu130-slim"
ENV_PATH = f"{REPO}/.env"
BRIDGE = "http://localhost:9223"
API = "http://localhost:8083"
DONE_FLAG = "/tmp/linkedin_ads_enable.done"
LOG_PREFIX = "[li-ads-enable]"


def log(msg):
    print(f"{LOG_PREFIX} {msg}", flush=True)


def load_env():
    env = {}
    with open(ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def req(url, data=None, headers=None, method=None, timeout=30):
    h = {"Content-Type": "application/json", "X-Platform": "linkedin"}
    h.update(headers or {})
    body = json.dumps(data).encode() if data is not None else None
    r = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return {"_http_error": e.code, "detail": e.read().decode()[:300]}
    except (OSError, ValueError) as e:
        return {"_error": str(e)}


def eval_js(expr, timeout=30):
    out = req(f"{BRIDGE}/session/evaluate", {"expression": expr}, timeout=timeout)
    return out.get("result", out)


def nav(url, timeout=60):
    return req(f"{BRIDGE}/session/navigate", {"url": url}, timeout=timeout)


def click_xy(x, y):
    return req(f"{BRIDGE}/session/mouse-click", {"x": x, "y": y})


def sidecar_cookies():
    try:
        with urllib.request.urlopen(f"{BRIDGE.replace('9223', '9225')}/debug/all-cookies", timeout=20) as r:
            d = json.loads(r.read())
        cookies = d.get("cookies", d)
        if isinstance(cookies, dict):
            return [{"name": k, "value": str(v), "domain": ".linkedin.com", "path": "/"}
                    for k, v in cookies.items()]
        return cookies
    except (OSError, ValueError) as e:
        log(f"sidecar cookies failed: {e}")
        return []


def claim_session():
    """Polite retries only — this is a scheduled non-urgent task."""
    for i in range(10):
        out = req(f"{BRIDGE}/session/start", {"platform": "linkedin"})
        if out.get("status") or "session" in str(out.get("platform", "")):
            log(f"bridge session claimed (attempt {i + 1})")
            return True
        log(f"bridge busy (attempt {i + 1}): {str(out)[:120]}")
        time.sleep(25)
    return False


def ensure_logged_in():
    state = eval_js(
        "({url:location.href,loggedIn:!!document.querySelector('img.global-nav__me-photo, .feed-identity-module, [data-control-name=identity_profile_photo]')})"
    )
    if isinstance(state, dict) and state.get("loggedIn"):
        return True
    cookies = sidecar_cookies()
    if not cookies:
        log("no sidecar cookies to transplant")
        return False
    out = req(f"{BRIDGE}/session/cookies", {"cookies": cookies})
    log(f"injected {out.get('added', 0)} sidecar cookies")
    nav("https://www.linkedin.com/feed/")
    time.sleep(6)
    state = eval_js("({url:location.href,loggedIn:!!document.querySelector('img.global-nav__me-photo, .feed-identity-module, [data-control-name=identity_profile_photo]')})")
    ok = isinstance(state, dict) and state.get("loggedIn")
    if not ok:
        log(f"still not logged in after transplant: {state}")
    return bool(ok)


def enable_product():
    """Developer console → app → Products → enable Advertising Reporting API."""
    nav("https://www.linkedin.com/developers/apps")
    time.sleep(6)
    state = eval_js("({url:location.href, body:document.body.innerText.slice(0,300)})")
    body = (state or {}).get("body", "") if isinstance(state, dict) else ""
    if "429" in str(state.get("url", "")) or "chrome-error" in str(state.get("url", "")):
        log("LinkedIn 429 wall still up — aborting this run")
        return False
    log(f"console page: {state.get('url', '')[:80]} | {body[:100]!r}")

    # Open the app (first app link on the page).
    apps = eval_js(
        "[...document.querySelectorAll('a')].filter(a=>a.offsetParent && /\\/developers\\/apps\\//.test(a.href||'')).map(a=>({href:a.href.slice(0,120),text:a.innerText.trim().slice(0,60)})).slice(0,10)"
    )
    log(f"app links: {apps}")
    if not isinstance(apps, list) or not apps:
        log("no app links found — console layout changed or not logged in")
        return False
    app_url = apps[0]["href"]
    nav(app_url)
    time.sleep(6)

    # Products tab
    nav(app_url.rstrip("/") + "/products")
    time.sleep(6)
    page = eval_js(
        "({url:location.href, body:document.body.innerText.slice(0,2500)})"
    )
    body = (page or {}).get("body", "") if isinstance(page, dict) else ""
    if "Advertising Reporting" not in body and "advertising" not in body.lower():
        log(f"products page has no advertising row: {body[:300]!r}")
        return False

    # Find the Advertising Reporting API row's request/enable control.
    btn = eval_js(
        """(()=>{
          const rows=[...document.querySelectorAll('*')].filter(e=>e.offsetParent&&e.children.length<6&&/advertising reporting/i.test(e.innerText||''));
          for(const row of rows){
            const b=[...row.querySelectorAll('button,a')].find(e=>e.offsetParent&&!e.disabled&&/request|add|enable|view access/i.test(e.innerText||''));
            if(b){const r=b.getBoundingClientRect();return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2),t:b.innerText.trim().slice(0,40)};}
          }
          return null;
        })()"""
    )
    log(f"product control: {btn}")
    if not isinstance(btn, dict) or "x" not in (btn or {}):
        # Maybe already enabled — check for a "added"/checkmark state.
        if re.search(r"advertising reporting[\s\S]{0,200}(added|enabled|view access)", body, re.IGNORECASE):
            log("Advertising Reporting API already enabled")
            return True
        log("no actionable control for Advertising Reporting API")
        return False
    click_xy(btn["x"], btn["y"])
    time.sleep(4)

    # Confirm any modal that appears.
    modal_btn = eval_js(
        """(()=>{
          const b=[...document.querySelectorAll('button')].find(e=>e.offsetParent&&!e.disabled&&/request access|confirm|submit|agree/i.test(e.innerText||''));
          if(!b) return null;
          const r=b.getBoundingClientRect();return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2),t:b.innerText.trim()};
        })()"""
    )
    if isinstance(modal_btn, dict) and modal_btn.get("x"):
        log(f"confirm modal: {modal_btn.get('t')}")
        click_xy(modal_btn["x"], modal_btn["y"])
        time.sleep(4)

    # Re-check: product should now show as added.
    nav(app_url.rstrip("/") + "/products")
    time.sleep(6)
    body = (eval_js("({body:document.body.innerText.slice(0,2500)})") or {}).get("body", "")
    if re.search(r"advertising reporting[\s\S]{0,200}(added|enabled|view access|requested)", body, re.IGNORECASE):
        log("Advertising Reporting API enabled/requested")
        return True
    log(f"post-click state unclear: {body[:300]!r}")
    return False


def flip_env():
    with open(ENV_PATH) as f:
        content = f.read()
    if re.search(r"^LINKEDIN_EXTRA_SCOPES=", content, re.MULTILINE):
        log("LINKEDIN_EXTRA_SCOPES already set")
        return True
    new = re.sub(
        r"^#\s*LINKEDIN_EXTRA_SCOPES=r_ads_reporting.*$",
        "LINKEDIN_EXTRA_SCOPES=r_ads_reporting",
        content,
        flags=re.MULTILINE,
    )
    if new == content:
        log("no commented LINKEDIN_EXTRA_SCOPES line found — adding it")
        new = content.rstrip() + "\nLINKEDIN_EXTRA_SCOPES=r_ads_reporting\n"
    with open(ENV_PATH, "w") as f:
        f.write(new)
    log("env flipped: LINKEDIN_EXTRA_SCOPES=r_ads_reporting")
    out = subprocess.run(
        ["/usr/bin/docker", "compose", "up", "-d", "--no-deps", "social-api"],
        cwd=REPO, capture_output=True, text=True, timeout=600, check=False,
    )
    log(f"api recreate rc={out.returncode} {out.stdout[-200:]}")
    time.sleep(15)
    return out.returncode == 0


def oauth_reconnect(env):
    """Drive the LinkedIn OAuth connect through the bridge consent flow."""
    # Login to SocialAuto for a JWT.
    import urllib.parse
    form = urllib.request.Request(
        f"{API}/api/v1/auth/login",
        data=urllib.parse.urlencode({
            "username": env["SOCIAL_ADMIN_EMAIL"],
            "password": env["SOCIAL_ADMIN_PASSWORD"],
        }).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(form, timeout=30) as r:
            token = json.loads(r.read())["access_token"]
    except (OSError, ValueError, KeyError) as e:
        log(f"socialauto login failed: {e}")
        return False

    # team_id from the linkedin account.
    acc = req(f"{API}/api/v1/accounts", headers={"Authorization": f"Bearer {token}"})
    items = acc if isinstance(acc, list) else acc.get("accounts") or acc.get("items") or []
    li = next((a for a in items if a.get("platform") == "linkedin"), None)
    if not li:
        log(f"no linkedin account in /accounts: {str(acc)[:200]}")
        return False
    team_id = li["team_id"]

    authz = req(f"{API}/api/v1/auth/oauth/linkedin/authorize?team_id={team_id}",
                headers={"Authorization": f"Bearer {token}"})
    url = authz.get("url") or authz.get("authorize_url") or authz.get("authorization_url")
    if not url:
        log(f"authorize response unexpected: {str(authz)[:200]}")
        return False
    if "r_ads_reporting" not in url:
        log("authorize URL missing r_ads_reporting — api not recreated with new env?")
        return False
    log("navigating consent flow")
    nav(url)
    time.sleep(8)

    # Consent: click Allow/Authorize if shown; previously-consented apps may
    # auto-redirect straight to the callback.
    for _ in range(6):
        cur = eval_js("({url:location.href})")
        u = (cur or {}).get("url", "") if isinstance(cur, dict) else ""
        if "social.cloudless.gr" in u or "/oauth/linkedin/callback" in u:
            log("callback reached — token exchange done server-side")
            return True
        btn = eval_js(
            """(()=>{
              const b=[...document.querySelectorAll('button')].find(e=>e.offsetParent&&!e.disabled&&/allow|authorize|accept|συνέχεια/i.test(e.innerText||e.value||''));
              if(!b) return null;
              const r=b.getBoundingClientRect();return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2),t:(b.innerText||b.value||'').trim()};
            })()"""
        )
        if isinstance(btn, dict) and btn.get("x"):
            log(f"consent button: {btn.get('t')}")
            click_xy(btn["x"], btn["y"])
            time.sleep(6)
        else:
            time.sleep(5)
    cur = eval_js("({url:location.href})")
    u = (cur or {}).get("url", "") if isinstance(cur, dict) else ""
    return "callback" in u or "social.cloudless.gr" in u


def verify(env):
    out = subprocess.run(
        ["/usr/bin/docker", "exec", "social-postgres", "psql", "-U", "social_user",
         "-d", "social_automation", "-t", "-c",
         "SELECT scopes FROM social_accounts WHERE platform='linkedin' LIMIT 1"],
        capture_output=True, text=True, timeout=30, check=False,
    )
    scopes = out.stdout
    ok = "r_ads_reporting" in scopes
    log(f"account scopes check: {'r_ads_reporting present' if ok else scopes.strip()[:150]}")
    return ok


def main():
    if os.path.exists(DONE_FLAG):
        log("done-flag present — already enabled, exiting")
        return
    env = load_env()

    log("stage A: claim bridge")
    if not claim_session():
        log("could not claim bridge — will retry next cron slot")
        sys.exit(2)

    if not ensure_logged_in():
        log("linkedin session not logged in — skipping this run")
        sys.exit(3)

    log("stage B: enable Advertising Reporting API product")
    if not enable_product():
        log("product enable failed/incomplete — will retry next cron slot")
        sys.exit(4)

    log("stage C: flip env + recreate api")
    if not flip_env():
        log("env/api recreate failed")
        sys.exit(5)

    log("stage D: OAuth re-consent for r_ads_reporting")
    if not oauth_reconnect(env):
        log("oauth reconnect incomplete — product enabled + env set; consent needs manual click")
        sys.exit(6)

    log("stage E: verify")
    if not verify(env):
        log("scopes not yet updated — may need a moment; flag not written")
        sys.exit(7)

    with open(DONE_FLAG, "w") as f:
        f.write("enabled\n")
    log("ALL STAGES COMPLETE — Advertising Reporting active")


if __name__ == "__main__":
    main()
