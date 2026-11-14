#!/usr/bin/env python3
"""instagram_reauth.py — unattended Instagram re-auth via the Facebook
browser sidecar session.

IG uses "Instagram API with Facebook Login": the authorize URL is a
facebook.com/dialog/oauth page, so the logged-in session on
facebook-browser-sidecar (:9226) can approve it — same unattended pattern
as tiktok_reauth.py, different sidecar API (/debug/navigate + /debug/eval).

Runs INSIDE the social-api container. DB access uses asyncpg (the only
sync-usable driver in the image — psycopg was never installed, which made
this lane exit 2 on every run until 2026-11-11). Flow:

  1. Find instagram social_account rows needing re-auth (status in
     active/expired/revoked, or token expiring within 1h) — cheap DB
     check, exits 0 when healthy.
  2. Pre-flight: probe the sidecar's FB session (facebook.com/me must not
     be a login wall). OAuth grants tokens for whoever is logged in — a
     dead/wrong session would "succeed" while the target stays revoked.
  3. Admin-login to the local API, mint a fresh authorize URL.
  4. Drive the sidecar: /debug/navigate the authorize URL -> /debug/eval
     clicks Continue/Allow through the FB permission dialog -> poll
     location.href until the oauth/instagram/callback URL appears.
  5. INSTAGRAM_REDIRECT_URI is http://localhost:8083/... which the sidecar
     container cannot reach — so instead of letting the browser finish the
     navigation, we extract code+state from the stuck callback URL and
     invoke the callback endpoint ourselves at 127.0.0.1:8000. The token
     exchange still sends the configured redirect_uri, so Meta sees an
     exact match.
  6. Verify the TARGET account row(s) flipped to active — not just "some"
     account.

State: /tmp/instagram-reauth-state.json (1 attempt/hour, 3 consecutive
failures → once/day).

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

# Script-mode runs put /app/scripts on sys.path, not /app — app.* imports
# (token decryption) need the repo root explicitly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

API = os.environ.get("SOCIAL_API_INTERNAL", "http://127.0.0.1:8000")
# Compose sets FACEBOOK_BROWSER_SIDECAR_URL (stack-ops wake proxy); the
# FACEBOOK_SIDECAR alias is kept for manual runs.
SIDECAR = (
    os.environ.get("FACEBOOK_BROWSER_SIDECAR_URL")
    or os.environ.get("FACEBOOK_SIDECAR")
    or "http://facebook-browser-sidecar:9226"
)
STATE = "/tmp/instagram-reauth-state.json"
# The @cloudless.gr account is login_type=business_login — it was connected
# through the "Instagram API with Facebook Login" onboarding flow, whose
# authorize endpoint is instagram-onboarding and whose callback lands on the
# FACEBOOK oauth callback (state carries p=instagram-onboarding). The plain
# /oauth/instagram/authorize endpoint 400s because INSTAGRAM_CLIENT_ID is
# intentionally empty (IG Graph here rides on the Facebook Login app).
AUTHORIZE_PATH = "/api/v1/auth/oauth/instagram-onboarding/authorize"
CALLBACK_PATH = "/api/v1/auth/oauth/facebook/callback"
CALLBACK_MARK = CALLBACK_PATH  # kept for _invoke_callback docstring compat


def _is_callback(url: str) -> bool:
    """True only when the browser actually LANDED on the callback — the
    consent dialog URL embeds the callback inside redirect_uri=..., so a
    substring match fires on the dialog itself."""
    try:
        return urllib.parse.urlparse(url).path.rstrip("/").endswith(CALLBACK_PATH)
    except Exception:
        return CALLBACK_MARK in url


MAX_CONSECUTIVE_FAILS = 3

# Logged-in check + display name on facebook.com/me (not a login wall).
_PROBE_IDENTITY_JS = """
(() => {
  const txt = (document.body && document.body.innerText || "").slice(0, 800);
  const url = location.href;
  if (/facebook\\.com\\/(login|recover|checkpoint)/.test(url)
      || /log in to facebook|create an account/i.test(txt.slice(0, 300))) {
    return JSON.stringify({ok: false, reason: "login_wall", url});
  }
  const m = txt.match(/([A-ZÀ-Þ][^\\n]{1,60})\\n[^\\n]*followers/);
  return JSON.stringify({ok: true, name: m ? m[1].trim() : null, url});
})()
"""

# Page snapshot for gate classification + clickable labels.
_SNAPSHOT_JS = """
(() => {
  const txt = (document.body && document.body.innerText || "").slice(0, 1200);
  const url = location.href;
  const title = document.title || "";
  const low = txt.toLowerCase();
  let gate = null;
  if (/captcha|verify you|verification code|unusual activity|are you a robot|two-factor|authentication code/i.test(txt)
      || document.querySelector('iframe[src*="captcha"], #captcha, [class*="captcha"]')) {
    gate = "captcha_or_verify";
  } else if (/log in to facebook|sign up for facebook|forgot password|create new account/i.test(txt)
             || /facebook\\.com\\/(login|recover|checkpoint)/.test(url)) {
    gate = "login_wall";
  }
  const btns = [...document.querySelectorAll(
      'button,[role=button],a[role=link],input[type=submit],[class*="uiButton"]')]
    .map(e => (e.innerText || e.value || e.getAttribute('aria-label') || "").trim())
    .filter(t => t && t.length < 60).slice(0, 15);
  return JSON.stringify({url, title, gate, text: txt.slice(0, 600), buttons: btns});
})()
"""

# Click through the FB OAuth permission dialog. Two-step flow: "Continue"
# (as the FB user) then "Allow"/"Continue" on the permissions screen.
# Account-chooser rows are <div role=button> or links — prefer one whose
# text mentions an IG handle, else the generic continue.
_CLICK_JS = """
(() => {
  const els = [...document.querySelectorAll(
      'button,[role=button],div[tabindex],a[role=link]')];
  const vis = e => { const r = e.getBoundingClientRect();
                     return r.width > 0 && r.height > 0; };
  const cand = els.filter(vis);
  const txt = e => (e.innerText || e.getAttribute('aria-label') || "").trim();
  // 1) explicit deny/cancel must never be clicked
  const deny = /cancel|decline|not now|maybe later|deny/i;
  // 2) IG account chooser: pick the row naming our handle when present
  const want = /cloudless/i;
  const pick = cand.find(e => want.test(txt(e)) && txt(e).length < 80
               && !deny.test(txt(e)));
  if (pick) { pick.click(); return 'picked-account:' + txt(pick).slice(0,40); }
  // 3) generic consent buttons
  const ok = /^(continue|allow|save|agree|next|confirm|got it)/i;
  const b = cand.find(e => ok.test(txt(e)) && txt(e).length < 40 && !deny.test(txt(e)));
  if (b) { b.click(); return 'clicked:' + txt(b).slice(0,30); }
  return 'no-button';
})()
"""

# FB OAuth sometimes bounces to facebook.com/login.php even with a valid
# session — it renders the "continue as <profile>" account chooser (no
# password field). That interstitial is auto-resolvable: click the exact
# "Continue"/"Next" button. Anything with a password input or without a
# matching button stays a human gate.
_LOGIN_WALL_CONTINUE_JS = """
(() => {
  if (document.querySelector('input[type=password]')) return 'has-password';
  const els = [...document.querySelectorAll(
      'button,[role=button],a[role=link],div[role=button]')];
  const vis = e => { const r = e.getBoundingClientRect();
                     return r.width > 0 && r.height > 0; };
  const txt = e => (e.innerText || e.getAttribute('aria-label') || "").trim();
  const b = els.filter(vis).find(e => /^(continue|next)$/i.test(txt(e)));
  if (b) { b.click(); return 'clicked:' + txt(b).slice(0, 30); }
  return 'no-continue';
})()
"""


def _req(url, data=None, headers=None, timeout=30, method=None):
    body = json.dumps(data).encode() if data is not None else None
    r = urllib.request.Request(url, data=body, method=method, headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(r, timeout=timeout) as f:
        raw = f.read()
        return json.loads(raw) if raw else {}


def _get(url, headers=None, timeout=30, follow=True):
    r = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(r, timeout=timeout) as f:
        raw = f.read()
        try:
            return json.loads(raw) if raw else {}
        except Exception:
            return {"_raw": raw[:300].decode("utf-8", "replace")}


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
    url = os.environ["DATABASE_URL"]
    for a, b in (("postgresql+asyncpg://", "postgresql://"), ("+asyncpg", "")):
        url = url.replace(a, b)
    return url


def _pg_fetchall(sql: str, *args):
    """Run a read query through asyncpg (installed in the image; psycopg
    and psycopg2 are not)."""
    import asyncio

    import asyncpg

    async def _go():
        conn = await asyncpg.connect(_db_url())
        try:
            return await conn.fetch(sql, *args)
        finally:
            await conn.close()

    return asyncio.run(_go())


def _graph_token_alive(enc) -> bool:
    """True when the stored Graph token still authenticates against
    graph.instagram.com/v26.0/me. Meta invalidates tokens server-side
    (OAuthException 190) while token_expires_at stays far in the future,
    so DB state alone can never detect this — probe once per run.
    Undecryptable tokens return True (detection falls back to DB state)."""
    try:
        from app.core.security import decrypt_token

        tok = decrypt_token(bytes(enc))
    except Exception:
        return True
    url = f"https://graph.instagram.com/v26.0/me?fields=id&access_token={urllib.parse.quote(tok)}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=15) as f:
            return f.status == 200
    except Exception:
        return False


def _targets():
    """Instagram accounts needing re-auth."""
    try:
        import asyncpg  # noqa: F401
    except ImportError:
        return None
    rows = _pg_fetchall(
        """SELECT id, account_id, username, status FROM social_accounts
           WHERE platform='instagram'
             AND (status IN ('expired','revoked','error')
                  OR (status='active'
                      AND token_expires_at < now() + interval '1 hour'))"""
    )
    out = [
        {
            "id": str(r["id"]),
            "account_id": r["account_id"] or "",
            "username": r["username"] or "",
            "status": r["status"],
        }
        for r in rows
    ]
    flagged = {t["id"] for t in out}
    # Server-side invalidation (OAuthException 190) leaves status='active'
    # and token_expires_at far in the future — probe Graph /me once per run.
    for r in _pg_fetchall(
        """SELECT id, account_id, username, access_token_enc
           FROM social_accounts
           WHERE platform='instagram' AND status='active'"""
    ):
        rid = str(r["id"])
        if rid in flagged:
            continue
        if not _graph_token_alive(r["access_token_enc"]):
            out.append(
                {
                    "id": rid,
                    "account_id": r["account_id"] or "",
                    "username": r["username"] or "",
                    "status": "active",
                    "note": "graph-token-dead",
                }
            )
            flagged.add(rid)
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
    # 3300s (not 3600): last_attempt is stamped at attempt START, and the
    # hourly beat can fire a few seconds early — a full-hour threshold
    # would skip every other beat.
    if now - s.get("last_attempt", 0) < 3300:
        return True
    return s.get("consecutive_fails", 0) >= MAX_CONSECUTIVE_FAILS and now - s.get("last_attempt", 0) < 86400


def _mark_start() -> None:
    """Stamp the attempt START so the hourly throttle measures from when
    work began, not when it ended (end-stamping made the lane every-2h)."""
    s = _state()
    s["last_attempt"] = time.time()
    _save_state(s)


def _mark(ok: bool) -> None:
    s = _state()
    s["consecutive_fails"] = 0 if ok else s.get("consecutive_fails", 0) + 1
    s["last_result"] = "ok" if ok else "fail"
    _save_state(s)


def _sidecar(path: str, payload: dict | None = None, timeout: int = 90) -> dict:
    if payload is None:
        return _get(f"{SIDECAR}{path}", timeout=timeout)
    return _req(f"{SIDECAR}{path}", data=payload, timeout=timeout)


def _eval(script: str, timeout: int = 60) -> dict:
    return _sidecar("/debug/eval", {"script": script}, timeout=timeout)


def _eval_str(script: str, timeout: int = 60) -> str:
    try:
        r = _eval(script, timeout=timeout)
        res = r.get("result")
        if isinstance(res, str):
            return res
        return json.dumps(res) if res is not None else ""
    except Exception as e:
        print(f"eval failed: {e}")
        return ""


def _page_url() -> str:
    """Browser-layer URL for callback detection.

    Page JS ``location.href`` reports ``chrome-error://chromewebdata/``
    when the OAuth callback navigation fails — localhost:8083 is
    unreachable from the sidecar container — which would hide the
    code+state forever. ``/debug/navigation-attempt`` records the real
    request URL on the Node side instead; ``/debug/page-text``'s ``url``
    is the fallback, page JS the last resort.
    """
    try:
        r = _get(f"{SIDECAR}/debug/navigation-attempt", timeout=30)
        url = r.get("attempted") or r.get("url") or ""
        if url:
            return url
    except Exception:
        pass
    try:
        r = _get(f"{SIDECAR}/debug/page-text", timeout=30)
        if r.get("url"):
            return r["url"]
    except Exception:
        pass
    return _eval_str("location.href", timeout=30)


def _page_snapshot() -> dict:
    raw = _eval_str(_SNAPSHOT_JS, timeout=45)
    try:
        return json.loads(raw) if raw else {}
    except Exception:
        return {"raw": raw[:300]}


def _dump_diagnostics(tag: str) -> None:
    snap = _page_snapshot()
    print(f"DIAG[{tag}] {json.dumps(snap, ensure_ascii=False)[:1200]}")
    try:
        shot = _sidecar("/screenshot", timeout=60)
        png = shot.get("png_base64") or shot.get("png") or shot.get("image")
        if png:
            path = f"/tmp/instagram-reauth-{int(time.time())}.png"
            with open(path, "wb") as f:
                f.write(base64.b64decode(png))
            print(f"SCREENSHOT {path} url={shot.get('url', '')[:120]} title={shot.get('title', '')[:80]}")
    except Exception as e:
        print(f"screenshot failed: {e}")


def _sidecar_identity() -> dict:
    """Who is the FB sidecar session? Probe facebook.com/me."""
    try:
        _sidecar("/debug/navigate", {"url": "https://www.facebook.com/me"}, timeout=90)
    except Exception as e:
        return {"ok": False, "reason": f"navigate:{e}"}
    time.sleep(4)
    raw = _eval_str(_PROBE_IDENTITY_JS, timeout=45)
    try:
        return json.loads(raw) if raw else {"ok": False, "reason": "empty"}
    except Exception:
        return {"ok": False, "reason": f"unparseable:{raw[:120]}"}


def _targets_refreshed(targets: list) -> tuple[bool, str]:
    try:
        import asyncpg  # noqa: F401
    except ImportError:
        return True, "no-asyncpg"
    ids = [t["account_id"] for t in targets if t.get("account_id")]
    if not ids:
        return True, "no-account-id"
    rows = _pg_fetchall(
        """SELECT account_id, username, status, access_token_enc,
                  COALESCE(token_expires_at > now() + interval '1 hour', false) AS fresh
           FROM social_accounts
           WHERE platform='instagram' AND account_id = ANY($1::text[])""",
        ids,
    )
    by_id = {r["account_id"]: r for r in rows}
    bad = []
    missing = []
    for i in ids:
        r = by_id.get(i)
        if r is None:
            missing.append(i)
        elif r["status"] != "active" or not r["fresh"]:
            bad.append((r["username"] or i, r["status"]))
        elif not _graph_token_alive(r["access_token_enc"]):
            bad.append((r["username"] or i, "graph-token-dead"))
    if not bad and not missing:
        return True, "all targets refreshed"
    parts = []
    if bad:
        parts.append("still-dead:" + ",".join(f"{u}({s})" for u, s in bad))
    if missing:
        parts.append("missing:" + ",".join(m[:12] for m in missing))
    return False, "; ".join(parts)


def _invoke_callback(callback_url: str) -> bool:
    """Fire the OAuth callback server-side.

    The browser is stuck on http://localhost:8083/... (unreachable from the
    sidecar container) but location.href already carries code+state. The
    backend route performs the code exchange; calling it directly at the
    in-container API address completes the flow identically.
    """
    q = callback_url.split("?", 1)[1] if "?" in callback_url else ""
    if not q:
        return False
    try:
        _get(f"{API}/api/v1/auth/oauth/instagram/callback?{q}", timeout=60)
        return True
    except Exception as e:
        print(f"callback invoke failed: {e}")
        return False


def main() -> int:
    if _throttled():
        print("throttled — recent attempt or max consecutive fails")
        return 0
    try:
        targets = _targets()
    except Exception as e:
        print(f"needs_reauth check failed: {e}")
        return 2
    if targets is None:
        print("asyncpg unavailable — cannot check targets")
        return 2
    if not targets:
        print("no instagram account needs re-auth")
        return 0

    _mark_start()
    print(f"instagram re-auth needed for {[t['username'] for t in targets]} — probing FB sidecar session")
    ident = _sidecar_identity()
    if not ident.get("ok"):
        print(f"sidecar identity probe failed: {ident.get('reason')} — FB session logged out; human re-login needed")
        _dump_diagnostics("preflight")
        _mark(False)
        return 3
    print(f"sidecar FB session ok (profile ~{ident.get('name')!r})")

    print("minting authorize URL")
    try:
        tok = _admin_token()
        teams = _get(f"{API}/api/v1/teams", headers={"Authorization": f"Bearer {tok}"})
        team_id = (teams[0] if isinstance(teams, list) else teams.get("items", [{}])[0]).get("id")
        auth = _get(f"{API}{AUTHORIZE_PATH}?team_id={team_id}", headers={"Authorization": f"Bearer {tok}"})
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
        _sidecar("/debug/navigate", {"url": url}, timeout=90)
    except Exception as e:
        print(f"sidecar navigate failed: {e}")
        _mark(False)
        return 2

    deadline = time.time() + 150
    gate = None
    while time.time() < deadline:
        cur = _page_url()
        if _is_callback(cur):
            break
        snap = _page_snapshot()
        gate = snap.get("gate")
        if gate == "login_wall":
            act = _eval_str(_LOGIN_WALL_CONTINUE_JS, timeout=30)
            if act.startswith("clicked"):
                print(f"login wall: continued as profile ({act})")
                time.sleep(4)
                continue
        if gate:
            print(f"human gate detected: {gate} — {snap.get('text', '')[:200]}")
            _dump_diagnostics(gate)
            _mark(False)
            return 3
        _eval(_CLICK_JS, timeout=30)
        time.sleep(4)

    cur = _page_url()
    if not _is_callback(cur):
        print("timed out waiting for OAuth callback")
        _dump_diagnostics("timeout")
        _mark(False)
        return 4

    print(f"callback URL reached: {cur[:120]}")
    if not _invoke_callback(cur):
        _dump_diagnostics("callback-invoke-failed")
        _mark(False)
        return 4

    time.sleep(5)
    try:
        ok, detail = _targets_refreshed(targets)
        print(f"post-verify: {detail}")
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
