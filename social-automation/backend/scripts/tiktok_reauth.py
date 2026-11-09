#!/usr/bin/env python3
"""tiktok_reauth.py — unattended TikTok OAuth re-authorization via the
browser sidecar session.

Runs INSIDE the social-api container (stdlib only). Flow:

  1. Find a tiktok social_account that needs re-auth (status='expired' or
     token_expires_at within 1h) — cheap DB check, exits 0 when healthy.
  2. Admin-login to the local API, mint a fresh PKCE authorize URL.
  3. Drive tiktok-browser-sidecar (persistent logged-in session):
     /browse the authorize URL -> /evaluate clicks Authorize -> poll
     location.href until the backend callback lands (the callback itself
     exchanges the code and stores tokens).
  4. Verify the account row refreshed. On login-wall/2FA/CAPTCHA or
     repeated failure, exit non-zero so cron/alerting can page a human.

State: /tmp/tiktok-reauth-state.json (attempt timestamps — max 1 run/hour,
max 3 consecutive failures before giving up until next day).

Exit codes: 0 ok/nothing-to-do, 2 lane-down, 3 human-gate-needed,
            4 flow-failed.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = os.environ.get("SOCIAL_API_INTERNAL", "http://127.0.0.1:8000")
SIDECAR = os.environ.get("TIKTOK_SIDECAR", "http://tiktok-browser-sidecar:9224")
STATE = "/tmp/tiktok-reauth-state.json"
CALLBACK_MARK = "social.cloudless.gr/api/v1/auth/oauth/tiktok/callback"
MAX_ATTEMPTS_PER_HOUR = 1
MAX_CONSECUTIVE_FAILS = 3


def _req(url, data=None, headers=None, timeout=30, method=None):
    body = json.dumps(data).encode() if data is not None else None
    r = urllib.request.Request(url, data=body, method=method,
                               headers={"Content-Type": "application/json",
                                        **(headers or {})})
    with urllib.request.urlopen(r, timeout=timeout) as f:
        raw = f.read()
        return json.loads(raw) if raw else {}


def _get(url, headers=None, timeout=30):
    r = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(r, timeout=timeout) as f:
        raw = f.read()
        return json.loads(raw) if raw else {}


def _admin_token() -> str:
    body = urllib.parse.urlencode({
        "username": os.environ["SOCIAL_ADMIN_EMAIL"],
        "password": os.environ["SOCIAL_ADMIN_PASSWORD"],
    }).encode()
    r = urllib.request.Request(f"{API}/api/v1/auth/login", data=body,
                               headers={"Content-Type":
                                        "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(r, timeout=20) as f:
        return json.loads(f.read())["access_token"]


def _db_url():
    """DATABASE_URL is asyncpg — rewrite to sync psycopg for a quick check."""
    url = os.environ["DATABASE_URL"]
    for a, b in (("postgresql+asyncpg://", "postgresql://"),
                 ("+asyncpg", "")):
        url = url.replace(a, b)
    return url


def needs_reauth() -> bool:
    """True when a tiktok account is expired or its access token lapses <1h."""
    try:
        import psycopg
    except ImportError:
        # fall back to the API surface
        return _needs_reauth_api()
    with psycopg.connect(_db_url()) as c, c.cursor() as cur:
        cur.execute(
            """SELECT count(*) FROM social_accounts
               WHERE platform='tiktok'
                 AND (status='expired'
                      OR token_expires_at < now() + interval '1 hour')""")
        return cur.fetchone()[0] > 0


def _needs_reauth_api() -> bool:
    from datetime import UTC, datetime, timedelta
    tok = _admin_token()
    teams = _get(f"{API}/api/v1/teams",
                 headers={"Authorization": f"Bearer {tok}"})
    team_id = (teams[0] if isinstance(teams, list)
               else teams.get("items", [{}])[0]).get("id")
    accs = _get(f"{API}/api/v1/accounts?team_id={team_id}",
                headers={"Authorization": f"Bearer {tok}"})
    items = accs if isinstance(accs, list) else accs.get("items", [])
    soon = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    for a in items:
        if a.get("platform") != "tiktok":
            continue
        exp = a.get("token_expires_at") or ""
        if a.get("status") == "expired" or (exp and exp < soon):
            return True
    return False


def _state() -> dict:
    try:
        return json.load(open(STATE))
    except Exception:
        return {}


def _save_state(s: dict) -> None:
    try:
        json.dump(s, open(STATE, "w"))
    except Exception:
        pass


def _throttled() -> bool:
    s = _state()
    now = time.time()
    if now - s.get("last_attempt", 0) < 3600:
        return True
    return s.get("consecutive_fails", 0) >= MAX_CONSECUTIVE_FAILS \
        and now - s.get("last_attempt", 0) < 86400


def _mark(ok: bool) -> None:
    s = _state()
    s["last_attempt"] = time.time()
    s["consecutive_fails"] = 0 if ok else s.get("consecutive_fails", 0) + 1
    s["last_result"] = "ok" if ok else "fail"
    _save_state(s)


def _sidecar(path: str, payload: dict, timeout: int = 90) -> dict:
    return _req(f"{SIDECAR}{path}", data=payload, timeout=timeout)


def _eval(expr: str, timeout: int = 60) -> dict:
    return _sidecar("/evaluate", {"expression": expr}, timeout=timeout)


def _page_url() -> str:
    r = _eval("location.href", timeout=30)
    return str(r.get("result") or "")


def main() -> int:
    if _throttled():
        print("throttled — recent attempt or max consecutive fails")
        return 0
    try:
        if not needs_reauth():
            print("no tiktok account needs re-auth")
            return 0
    except Exception as e:
        print(f"needs_reauth check failed: {e}")
        return 2

    print("tiktok re-auth needed — minting authorize URL")
    try:
        tok = _admin_token()
        teams = _get(f"{API}/api/v1/teams",
                     headers={"Authorization": f"Bearer {tok}"})
        team_id = (teams[0] if isinstance(teams, list)
                   else teams.get("items", [{}])[0]).get("id")
        auth = _get(f"{API}/api/v1/auth/oauth/tiktok/authorize"
                    f"?team_id={team_id}",
                    headers={"Authorization": f"Bearer {tok}"})
        url = auth.get("authorization_url")
        if not url:
            print(f"no authorization_url: {auth}")
            _mark(False)
            return 4
    except Exception as e:
        print(f"authorize-url mint failed: {e}")
        _mark(False)
        return 2

    print("sidecar: browsing authorize URL")
    try:
        _sidecar("/browse", {"url": url}, timeout=90)
    except Exception as e:
        print(f"sidecar browse failed: {e}")
        _mark(False)
        return 2

    # TikTok SPA renders the authorize button async — poll + click.
    click_js = """
    (() => {
      const els = [...document.querySelectorAll('button,[role=button],a,div[tabindex]')];
      const b = els.find(e => /^(authorize|allow|agree|continue|confirm)/i
        .test((e.innerText||'').trim()) && (e.innerText||'').trim().length < 40);
      if (b) { b.click(); return 'clicked:' + b.innerText.trim().slice(0,30); }
      const txt = (document.body.innerText||'').slice(0,300);
      return 'no-button :: ' + txt.replace(/\\n/g,' | ');
    })()
    """
    deadline = time.time() + 120
    while time.time() < deadline:
        cur = _page_url()
        if CALLBACK_MARK in cur:
            break
        if "/login" in cur or "passport" in cur:
            print(f"login wall at {cur[:120]} — human gate")
            _mark(False)
            return 3
        _eval(click_js, timeout=30)
        time.sleep(4)

    # wait for the backend callback to complete the code exchange
    deadline = time.time() + 90
    while time.time() < deadline:
        cur = _page_url()
        if CALLBACK_MARK not in cur and "social.cloudless.gr" in cur:
            break  # landed on frontend success/error page
        time.sleep(3)

    final_url = _page_url()
    print(f"final url: {final_url[:140]}")
    ok = "social.cloudless.gr" in final_url and "error" not in final_url.lower()
    if ok:
        # The OAuth flow authorizes whichever TikTok account the sidecar is
        # logged into — that may NOT be the expired row (seen 2026-11-09: a
        # second cloudless.gr account exists; the sidecar refreshed the
        # already-active one). Verify the expired account actually refreshed
        # before declaring success.
        time.sleep(5)
        try:
            ok = _expired_account_refreshed()
        except Exception as e:
            print(f"post-verify failed: {e}")
            ok = False
    _mark(ok)
    print("reauth", "succeeded" if ok else "finished-with-error")
    return 0 if ok else 4


def _expired_account_refreshed() -> bool:
    """True when no tiktok account still needs re-auth."""
    return not needs_reauth()


if __name__ == "__main__":
    sys.exit(main())
