#!/usr/bin/env python3
"""threads_reauth.py — unattended Threads re-auth via the shared browser
bridge (browser-novnc:9223).

Threads OAuth (threads.net/oauth/authorize) redirects to an Instagram
login, and the bridge session is logged in as the IG identity — so the
"Continue As" consent can be clicked unattended, same pattern as
tiktok_reauth.py / instagram_reauth.py but driving the shared bridge
(/session/navigate + /session/evaluate, 409-busy retry built in).

Unlike instagram_reauth, THREADS_REDIRECT_URI is the PUBLIC
https://social.cloudless.gr/... URL — the browser completes the callback
itself; no server-side invoke needed.

Runs INSIDE the social-api container (stdlib only). Flow:

  1. Find threads social_account rows needing re-auth (status in
     active/expired/revoked, or token expiring within 1h).
  2. Pre-flight: probe the bridge's threads.net session — must be logged
     in (not a login wall). Wrong IG profile → human gate.
  3. Admin-login to the local API, mint a fresh authorize URL.
  4. Drive the bridge: navigate authorize URL -> eval-click
     Continue/Allow through the consent dialog -> poll location.href
     until /api/v1/auth/oauth/threads/callback appears.
  5. Verify the TARGET account row(s) flipped to active.

State: /tmp/threads-reauth-state.json (1 attempt/hour, 3 consecutive
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
import urllib.error

API = os.environ.get("SOCIAL_API_INTERNAL", "http://127.0.0.1:8000")
BRIDGE = os.environ.get("BROWSER_BRIDGE_URL", "http://browser-novnc:9223")
STATE = "/tmp/threads-reauth-state.json"
CALLBACK_MARK = "/api/v1/auth/oauth/threads/callback"
MAX_CONSECUTIVE_FAILS = 3

# Login-wall check on threads.net + best-effort identity read.
_PROBE_IDENTITY_JS = """
(() => {
  const txt = (document.body && document.body.innerText || "").slice(0, 800);
  const url = location.href;
  if (/log in|sign up/i.test(txt.slice(0, 300))
      && !/for you|following|activity|profile/i.test(txt)) {
    return JSON.stringify({ok: false, reason: "login_wall", url});
  }
  return JSON.stringify({ok: true, url});
})()
"""

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
  } else if (/log in to|sign up for|forgot password/i.test(txt)
             || /\\/login|\\/accounts\\/login/.test(url)) {
    gate = "login_wall";
  }
  const btns = [...document.querySelectorAll(
      'button,[role=button],a[role=link],input[type=submit],[class*="uiButton"],[class*="x1i10hfl"]')]
    .map(e => (e.innerText || e.value || e.getAttribute('aria-label') || "").trim())
    .filter(t => t && t.length < 60).slice(0, 15);
  return JSON.stringify({url, title, gate, text: txt.slice(0, 600), buttons: btns});
})()
"""

# Click through Threads/IG consent: "Continue as {user}" account chooser
# first (prefer the cloudless handle), then generic Continue/Allow.
_CLICK_JS = """
(() => {
  const els = [...document.querySelectorAll(
      'button,[role=button],div[tabindex],a[role=link]')];
  const vis = e => { const r = e.getBoundingClientRect();
                     return r.width > 0 && r.height > 0; };
  const cand = els.filter(vis);
  const txt = e => (e.innerText || e.getAttribute('aria-label') || "").trim();
  const deny = /cancel|decline|not now|maybe later|deny|log out/i;
  const want = /cloudless/i;
  const pick = cand.find(e => want.test(txt(e)) && txt(e).length < 80
               && !deny.test(txt(e)));
  if (pick) { pick.click(); return 'picked-account:' + txt(pick).slice(0,40); }
  const ok = /continue|allow|authorize|approve|next|confirm|got it/i;
  const b = cand.find(e => ok.test(txt(e)) && txt(e).length < 60 && !deny.test(txt(e)));
  if (b) { b.click(); return 'clicked:' + txt(b).slice(0,30); }
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


def _targets():
    """Threads accounts needing re-auth."""
    try:
        import psycopg
    except ImportError:
        return None
    out = []
    with psycopg.connect(_db_url()) as c, c.cursor() as cur:
        cur.execute(
            """SELECT id, account_id, username, status FROM social_accounts
               WHERE platform='threads'
                 AND (status IN ('expired','revoked')
                      OR (status='active'
                          AND token_expires_at < now() + interval '1 hour'))"""
        )
        for row in cur.fetchall():
            out.append({"id": str(row[0]), "account_id": row[1] or "", "username": row[2] or "", "status": row[3]})
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


def _pin() -> None:
    try:
        urllib.request.urlopen(
            urllib.request.Request(
                "http://stack-ops:8787/keepawake/browser-novnc?ttl=600",
                method="POST", data=b""), timeout=8)
    except Exception:
        pass


def _bridge_eval(expr: str, tries: int = 4) -> dict:
    """/session/evaluate with 409-busy retry (shared bridge contention)."""
    _pin()
    last = None
    for _ in range(tries):
        try:
            r = _req(f"{BRIDGE}/session/evaluate",
                     data={"expression": expr}, timeout=30)
            if r.get("error"):
                last = r["error"]
                if "409" in str(last) or "busy" in str(last).lower():
                    time.sleep(15)
                    continue
                return r
            return r
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 409:
                time.sleep(15)
                continue
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}
    return {"error": f"busy after {tries} tries: {last}"}


def _eval_str(expr: str, timeout_tries: int = 4) -> str:
    try:
        r = _bridge_eval(expr, tries=timeout_tries)
        res = r.get("result")
        if isinstance(res, str):
            return res
        return json.dumps(res) if res is not None else ""
    except Exception as e:
        print(f"eval failed: {e}")
        return ""


def _navigate(url: str) -> dict:
    _pin()
    return _req(f"{BRIDGE}/session/navigate", data={"url": url}, timeout=60)


def _page_url() -> str:
    return _eval_str("location.href")


def _page_snapshot() -> dict:
    raw = _eval_str(_SNAPSHOT_JS)
    try:
        return json.loads(raw) if raw else {}
    except Exception:
        return {"raw": raw[:300]}


def _dump_diagnostics(tag: str) -> None:
    snap = _page_snapshot()
    print(f"DIAG[{tag}] {json.dumps(snap, ensure_ascii=False)[:1200]}")


def _bridge_identity() -> dict:
    """Is the bridge's threads.net session logged in?"""
    try:
        _navigate("https://www.threads.net/")
    except Exception as e:
        return {"ok": False, "reason": f"navigate:{e}"}
    time.sleep(5)
    raw = _eval_str(_PROBE_IDENTITY_JS)
    try:
        return json.loads(raw) if raw else {"ok": False, "reason": "empty"}
    except Exception:
        return {"ok": False, "reason": f"unparseable:{raw[:120]}"}


def _targets_refreshed(targets: list) -> tuple[bool, str]:
    try:
        import psycopg
    except ImportError:
        return True, "no-psycopg"
    ids = [t["account_id"] for t in targets if t.get("account_id")]
    if not ids:
        return True, "no-account-id"
    with psycopg.connect(_db_url()) as c, c.cursor() as cur:
        cur.execute(
            """SELECT account_id, username, status,
                      COALESCE(token_expires_at > now() + interval '1 hour', false) AS fresh
               FROM social_accounts
               WHERE platform='threads' AND account_id = ANY(%s)""",
            (ids,),
        )
        rows = cur.fetchall()
    by_id = {r[0]: r for r in rows}
    bad = [by_id[i] for i in ids if i in by_id and (by_id[i][2] != "active" or not by_id[i][3])]
    missing = [i for i in ids if i not in by_id]
    if not bad and not missing:
        return True, "all targets refreshed"
    parts = []
    if bad:
        parts.append("still-dead:" + ",".join(f"{r[1] or r[0]}({r[2]})" for r in bad))
    if missing:
        parts.append("missing:" + ",".join(m[:12] for m in missing))
    return False, "; ".join(parts)


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
        print("psycopg unavailable — cannot check targets")
        return 2
    if not targets:
        print("no threads account needs re-auth")
        return 0

    print(f"threads re-auth needed for {[t['username'] for t in targets]} — probing bridge session")
    ident = _bridge_identity()
    if not ident.get("ok"):
        print(f"bridge session probe failed: {ident.get('reason')} — threads.net logged out; human re-login needed")
        _dump_diagnostics("preflight")
        _mark(False)
        return 3
    print("bridge threads session ok")

    print("minting authorize URL")
    try:
        tok = _admin_token()
        teams = _get(f"{API}/api/v1/teams", headers={"Authorization": f"Bearer {tok}"})
        team_id = (teams[0] if isinstance(teams, list) else teams.get("items", [{}])[0]).get("id")
        auth = _get(f"{API}/api/v1/auth/oauth/threads/authorize?team_id={team_id}", headers={"Authorization": f"Bearer {tok}"})
        url = auth.get("authorization_url")
        if not url:
            print(f"no authorization_url: {auth}")
            _mark(False)
            return 4
    except Exception as e:
        print(f"authorize-url mint failed: {e}")
        _mark(False)
        return 2

    print("bridge: browsing authorize URL")
    try:
        _navigate(url)
    except Exception as e:
        print(f"bridge navigate failed: {e}")
        _mark(False)
        return 2

    deadline = time.time() + 180
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
        _bridge_eval(_CLICK_JS, tries=2)
        time.sleep(5)

    cur = _page_url()
    if CALLBACK_MARK not in cur:
        print("timed out waiting for OAuth callback")
        _dump_diagnostics("timeout")
        _mark(False)
        return 4

    print(f"callback reached: {cur[:120]}")
    # Public redirect_uri — the browser completes the exchange itself.
    time.sleep(6)

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
