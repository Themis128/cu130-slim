#!/usr/bin/env python3
"""tiktok_reauth.py — unattended TikTok OAuth re-authorization via the
browser sidecar session.

Runs INSIDE the social-api container (stdlib only). Flow:

  1. Find tiktok social_account rows that need re-auth (status='expired' or
     token_expires_at within 1h) — cheap DB check, exits 0 when healthy.
  2. Pre-flight: probe the sidecar's logged-in TikTok identity via the
     passport endpoint. OAuth grants tokens for whoever is logged in — if
     the session is a different @account than the expired row, the flow
     can "succeed" while the target stays expired (seen 2026-11-09/10).
     Mismatch or dead session -> exit 3 (human gate) BEFORE burning an
     attempt.
  3. Admin-login to the local API, mint a fresh PKCE authorize URL.
  4. Drive tiktok-browser-sidecar (persistent logged-in session):
     /browse the authorize URL -> /evaluate clicks Authorize -> poll
     location.href until the backend callback lands (the callback itself
     exchanges the code and stores tokens).
  5. Verify the TARGET account row(s) refreshed — not just "some" account.
  6. On any failure, dump a DOM diagnostic (url/title/text/buttons) and a
     screenshot to /tmp so the cron wrapper can ship it to ~/logs — rc=4
     without a screenshot was undebuggable (2026-11-10).

State: /tmp/tiktok-reauth-state.json (attempt timestamps — max 1 run/hour,
max 3 consecutive failures before giving up until next day).

Exit codes: 0 ok/nothing-to-do, 2 lane-down, 3 human-gate-needed,
            4 flow-failed.
"""

import base64
import json
import os
import sys
import time
import urllib.parse
import urllib.request

API = os.environ.get("SOCIAL_API_INTERNAL", "http://127.0.0.1:8000")
SIDECAR = os.environ.get("TIKTOK_SIDECAR", "http://tiktok-browser-sidecar:9224")
STATE = "/tmp/tiktok-reauth-state.json"
CALLBACK_MARK = "social.cloudless.gr/api/v1/auth/oauth/tiktok/callback"
MAX_ATTEMPTS_PER_HOUR = 1
MAX_CONSECUTIVE_FAILS = 3

# JS probes run inside the sidecar page. Keep them string-returning —
# /evaluate serializes the expression result.
_PROBE_IDENTITY_JS = """
(async () => {
  try {
    const r = await fetch("https://www.tiktok.com/passport/web/account/info/",
                          {credentials: "include"});
    const d = await r.json();
    const u = d && d.data;
    if (r.status === 200 && u && u.username) {
      return JSON.stringify({ok: true, username: u.username,
                             user_id: u.user_id_str || null});
    }
    return JSON.stringify({ok: false, reason: "no_username", status: r.status});
  } catch (e) { return JSON.stringify({ok: false, reason: String(e)}); }
})()
"""

# One-shot page snapshot: gate classification + clickable-element labels.
# Returned as a JSON string; parsed by _page_snapshot().
_SNAPSHOT_JS = """
(() => {
  const txt = (document.body && document.body.innerText || "").slice(0, 1200);
  const url = location.href;
  const title = document.title || "";
  const low = txt.toLowerCase();
  let gate = null;
  if (/captcha|verify you|verification code|unusual activity|are you a robot/i.test(txt)
      || document.querySelector('iframe[src*="captcha"], #captcha, [class*="captcha"]')) {
    gate = "captcha_or_verify";
  } else if (/log in to tiktok|sign up|enter your password|two-factor|2fa|authentication code/i.test(txt)
             || /\\/login|passport/.test(url)) {
    gate = "login_wall";
  }
  const btns = [...document.querySelectorAll(
      'button,[role=button],a[href],input[type=submit]')]
    .map(e => (e.innerText || e.value || e.getAttribute('aria-label') || "").trim())
    .filter(t => t && t.length < 60).slice(0, 15);
  return JSON.stringify({url, title, gate, text: txt.slice(0, 600), buttons: btns});
})()
"""

# Click the TikTok consent button. TikTok's authorize page renders an
# "Authorize" button inside a React SPA — poll until it exists. Buttons
# only: matching <a>/div[tabindex] previously navigated the SPA to a
# profile page and killed the flow (2026-11-10). When consent is already
# granted TikTok auto-redirects — no click needed at all.
_CLICK_JS = """
(() => {
  if (!/tiktok\\.com/.test(location.host)) return 'off-tiktok';
  const els = [...document.querySelectorAll('button,[role=button]')];
  const b = els.find(e => /authorize|allow|agree|continue|confirm/i
    .test((e.innerText||'').trim()) && (e.innerText||'').trim().length < 40
    && !/log ?in|sign ?up|cancel|decline|deny/i.test(e.innerText||''));
  if (b) { b.click(); return 'clicked:' + b.innerText.trim().slice(0,30); }
  return 'no-button';
})()
"""


def _req(url, data=None, headers=None, timeout=30, method=None):
    body = json.dumps(data).encode() if data is not None else None
    r = urllib.request.Request(url, data=body, method=method, headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(r, timeout=timeout) as f:
        raw = f.read()
        return json.loads(raw) if raw else {}


def _get(url, headers=None, timeout=30):
    r = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(r, timeout=timeout) as f:
        raw = f.read()
        return json.loads(raw) if raw else {}


def _admin_token() -> str:
    body = urllib.parse.urlencode(
        {
            "username": os.environ["SOCIAL_ADMIN_EMAIL"],
            "password": os.environ["SOCIAL_ADMIN_PASSWORD"],
        }
    ).encode()
    r = urllib.request.Request(f"{API}/api/v1/auth/login", data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(r, timeout=20) as f:
        return json.loads(f.read())["access_token"]


def _db_url():
    """DATABASE_URL is asyncpg — rewrite to sync psycopg for a quick check."""
    url = os.environ["DATABASE_URL"]
    for a, b in (("postgresql+asyncpg://", "postgresql://"), ("+asyncpg", "")):
        url = url.replace(a, b)
    return url


def _targets():
    """TikTok accounts needing re-auth: [{'id','account_id','username'}]."""
    try:
        import psycopg
    except ImportError:
        return None  # caller falls back to the API surface
    out = []
    with psycopg.connect(_db_url()) as c, c.cursor() as cur:
        cur.execute(
            """SELECT id, account_id, username FROM social_accounts
               WHERE platform='tiktok'
                 AND status IN ('active','expired')
                 AND (status='expired'
                      OR token_expires_at < now() + interval '1 hour')"""
        )
        for row in cur.fetchall():
            out.append({"id": str(row[0]), "account_id": row[1] or "", "username": row[2] or ""})
    return out


def _needs_reauth_api() -> list:
    from datetime import UTC, datetime, timedelta

    tok = _admin_token()
    teams = _get(f"{API}/api/v1/teams", headers={"Authorization": f"Bearer {tok}"})
    team_id = (teams[0] if isinstance(teams, list) else teams.get("items", [{}])[0]).get("id")
    accs = _get(f"{API}/api/v1/accounts?team_id={team_id}", headers={"Authorization": f"Bearer {tok}"})
    items = accs if isinstance(accs, list) else accs.get("items", [])
    soon = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    out = []
    for a in items:
        if a.get("platform") != "tiktok":
            continue
        exp = a.get("token_expires_at") or ""
        if a.get("status") not in ("active", "expired"):
            continue
        if a.get("status") == "expired" or (exp and exp < soon):
            out.append({"id": a.get("id", ""), "account_id": a.get("account_id", ""), "username": a.get("username") or ""})
    return out


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
    return s.get("consecutive_fails", 0) >= MAX_CONSECUTIVE_FAILS and now - s.get("last_attempt", 0) < 86400


def _mark(ok: bool) -> None:
    s = _state()
    s["last_attempt"] = time.time()
    s["consecutive_fails"] = 0 if ok else s.get("consecutive_fails", 0) + 1
    s["last_result"] = "ok" if ok else "fail"
    _save_state(s)


def _sidecar(path: str, payload: dict | None = None, timeout: int = 90, method: str | None = None) -> dict:
    if payload is None:
        return _get(f"{SIDECAR}{path}", timeout=timeout)
    return _req(f"{SIDECAR}{path}", data=payload, timeout=timeout, method=method)


def _eval(expr: str, timeout: int = 60) -> dict:
    return _sidecar("/evaluate", {"expression": expr}, timeout=timeout)


def _eval_str(expr: str, timeout: int = 60) -> str:
    """Evaluate JS, return the string result ('' on any failure)."""
    try:
        r = _eval(expr, timeout=timeout)
        res = r.get("result")
        return res if isinstance(res, str) else (json.dumps(res) if res is not None else "")
    except Exception as e:
        print(f"eval failed: {e}")
        return ""


def _page_url() -> str:
    return _eval_str("location.href", timeout=30)


def _norm_username(u: str) -> str:
    return (u or "").strip().lstrip("@").lower()


def _sidecar_identity() -> dict:
    """Who is the sidecar's TikTok session logged in as?

    Returns {'ok': bool, 'username': str|None, 'reason': str|None}.
    Probes https://www.tiktok.com/passport/web/account/info/ from inside
    the page — the endpoint answers for the cookie session, no signing.
    """
    # The fetch is origin-bound: the page must be on tiktok.com first.
    try:
        _sidecar("/browse", {"url": "https://www.tiktok.com/"}, timeout=90)
    except Exception as e:
        return {"ok": False, "username": None, "reason": f"browse:{e}"}
    raw = _eval_str(_PROBE_IDENTITY_JS, timeout=45)
    try:
        return json.loads(raw) if raw else {"ok": False, "reason": "empty"}
    except Exception:
        return {"ok": False, "reason": f"unparseable:{raw[:120]}"}


def _page_snapshot() -> dict:
    raw = _eval_str(_SNAPSHOT_JS, timeout=45)
    try:
        return json.loads(raw) if raw else {}
    except Exception:
        return {"raw": raw[:300]}


def _dump_diagnostics(tag: str) -> None:
    """Print a compact DOM dump + save a screenshot for post-mortem."""
    snap = _page_snapshot()
    print(f"DIAG[{tag}] {json.dumps(snap, ensure_ascii=False)[:1200]}")
    try:
        shot = _sidecar("/screenshot", timeout=60)
        png = shot.get("png_base64")
        if png:
            path = f"/tmp/tiktok-reauth-{int(time.time())}.png"
            with open(path, "wb") as f:
                f.write(base64.b64decode(png))
            print(f"SCREENSHOT {path} url={shot.get('url', '')[:120]} title={shot.get('title', '')[:80]}")
    except Exception as e:
        print(f"screenshot failed: {e}")


def _targets_refreshed(targets: list) -> tuple[bool, str]:
    """True when every target row no longer needs re-auth.

    Returns (all_clear, detail) where detail names what still needs auth
    or which foreign account got refreshed instead.
    """
    try:
        import psycopg
    except ImportError:
        return (not _needs_reauth_api(), "api-check")
    ids = [t["account_id"] for t in targets if t.get("account_id")]
    if not ids:
        return (not _targets_needing_reauth_db(), "no-account-id")
    with psycopg.connect(_db_url()) as c, c.cursor() as cur:
        cur.execute(
            """SELECT account_id, username, status,
                      token_expires_at < now() + interval '1 hour' AS stale
               FROM social_accounts
               WHERE platform='tiktok' AND account_id = ANY(%s)""",
            (ids,),
        )
        rows = cur.fetchall()
    by_id = {r[0]: r for r in rows}
    stale = [by_id[i] for i in ids if i in by_id and (by_id[i][2] != "active" or by_id[i][3])]
    missing = [i for i in ids if i not in by_id]
    if not stale and not missing:
        return True, "all targets refreshed"
    parts = []
    if stale:
        parts.append("still-expired:" + ",".join(f"{r[1] or r[0]}({r[0][:12]}…)" for r in stale))
    if missing:
        parts.append("missing:" + ",".join(m[:12] for m in missing))
    return False, "; ".join(parts)


def _targets_needing_reauth_db() -> bool:
    try:
        return bool(_targets())
    except Exception:
        return True  # can't tell -> treat as still needed


def _foreign_refresh(targets: list) -> str | None:
    """account_id of a tiktok row refreshed in the last 15 min that is NOT
    a reauth target — i.e. the sidecar authorized a different account."""
    try:
        import psycopg
    except ImportError:
        return None
    target_ids = {t["account_id"] for t in targets}
    with psycopg.connect(_db_url()) as c, c.cursor() as cur:
        cur.execute(
            """SELECT account_id, username FROM social_accounts
               WHERE platform='tiktok'
                 AND updated_at > now() - interval '15 minutes'
                 AND account_id <> ALL(%s)""",
            (list(target_ids),),
        )
        rows = cur.fetchall()
    if rows:
        return f"{rows[0][1] or rows[0][0]}({rows[0][0][:12]}…)"
    return None


def main() -> int:
    if _throttled():
        print("throttled — recent attempt or max consecutive fails")
        return 0
    try:
        targets = _targets()
        if targets is None:
            targets = _needs_reauth_api()
    except Exception as e:
        print(f"needs_reauth check failed: {e}")
        return 2
    if not targets:
        print("no tiktok account needs re-auth")
        return 0

    want = {_norm_username(t["username"]) for t in targets} - {""}
    print(f"tiktok re-auth needed for {sorted(want) or targets} — checking sidecar session identity")

    # Pre-flight: OAuth authorizes whoever the sidecar is logged into.
    ident = _sidecar_identity()
    if not ident.get("ok"):
        print(f"sidecar identity probe failed: {ident.get('reason')} — session likely logged out; human re-login needed")
        _dump_diagnostics("preflight")
        _mark(False)
        return 3
    sid = _norm_username(ident.get("username") or "")
    print(f"sidecar logged in as @{sid} (uid {ident.get('user_id')})")
    if want and sid not in want:
        print(
            f"WRONG ACCOUNT: sidecar session @{sid} != target "
            f"{sorted(want)} — flow would refresh the wrong row; "
            "log into the target TikTok account on the sidecar"
        )
        _dump_diagnostics("wrong-account")
        _mark(False)
        return 3

    print("minting authorize URL")
    try:
        tok = _admin_token()
        teams = _get(f"{API}/api/v1/teams", headers={"Authorization": f"Bearer {tok}"})
        team_id = (teams[0] if isinstance(teams, list) else teams.get("items", [{}])[0]).get("id")
        auth = _get(f"{API}/api/v1/auth/oauth/tiktok/authorize?team_id={team_id}", headers={"Authorization": f"Bearer {tok}"})
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

    deadline = time.time() + 120
    gate = None
    while time.time() < deadline:
        cur = _page_url()
        if CALLBACK_MARK in cur:
            break
        snap = _page_snapshot()
        gate = snap.get("gate")
        if gate:
            print(f"human gate detected: {gate} — {snap.get('text', '')[:200]}")
            _dump_diagnostics(gate)
            _mark(False)
            return 3
        _eval(_CLICK_JS, timeout=30)
        time.sleep(4)

    if CALLBACK_MARK not in _page_url():
        print("timed out waiting for OAuth callback")
        _dump_diagnostics("timeout")
        _mark(False)
        return 4

    # The backend callback is a JSON API — it renders
    # {"message":"tiktok account connected successfully","account_id":…}
    # (or {"detail": …} on failure) IN-PLACE; it never redirects to a
    # frontend page, so the URL keeps CALLBACK_MARK forever. Poll the
    # rendered body for the verdict instead of waiting for navigation
    # (2026-11-12: every real success was reported finished-with-error).
    deadline = time.time() + 90
    body = ""
    while time.time() < deadline:
        body = _eval_str("document.body ? document.body.innerText : ''", timeout=30)
        low = body.lower()
        if "connected successfully" in low or '"detail"' in low or "error" in low[:300]:
            break
        time.sleep(3)

    final_url = _page_url()
    print(f"final url: {final_url[:140]}")
    print(f"callback body: {body[:200]}")
    low = body.lower()
    api_ok = "connected successfully" in low
    api_fail = bool(body.strip()) and not api_ok
    # Keep the legacy URL heuristic for any future redirecting callback.
    redirect_ok = (
        CALLBACK_MARK not in final_url
        and "social.cloudless.gr" in final_url
        and "error" not in final_url.lower()
    )
    ok = api_ok or (redirect_ok and not api_fail)
    if api_fail and not api_ok:
        print(f"callback reported failure: {body[:300]}")
    if ok:
        # The OAuth flow authorizes whichever TikTok account the sidecar is
        # logged into — verify the TARGET rows actually refreshed, not just
        # that some account did (seen 2026-11-09: a second cloudless.gr
        # account exists; the sidecar refreshed the already-active one).
        time.sleep(5)
        try:
            ok, detail = _targets_refreshed(targets)
            print(f"post-verify: {detail}")
            if not ok:
                foreign = _foreign_refresh(targets)
                if foreign:
                    # OAuth for the sidecar's @account just resolved to a
                    # DIFFERENT open_id than the stale target's stored
                    # account_id — the target row is orphaned (dead app
                    # client_key or deleted/recreated TikTok account) and
                    # NO consent flow can ever re-authorize it.
                    print(
                        f"ORPHAN: OAuth resolved the session to {foreign}; "
                        f"stale target account_id(s) "
                        f"{[t['account_id'][:12] for t in targets]} are "
                        "unreachable — revoke/delete the stale row(s)"
                    )
                    _dump_diagnostics("orphan")
                    _mark(False)
                    return 3
        except Exception as e:
            print(f"post-verify failed: {e}")
            ok = False
    if not ok:
        _dump_diagnostics("finished-with-error")
    _mark(ok)
    print("reauth", "succeeded" if ok else "finished-with-error")
    return 0 if ok else 4


if __name__ == "__main__":
    sys.exit(main())
