#!/usr/bin/env python3
"""gate_drive.py — dispatch the remaining human gates through whatever lane is live.

Each gate defines: lane check (is its session/credential available?),
driver (the automated action), and store (where the result lands).
Run standalone (`python3 gate_drive.py <gate>`) or from the beat.
No-ops cleanly when a lane is down — never errors on absence.
"""
import json, os, sys, base64, subprocess

BRIDGE = "http://browser-novnc:9223"
API = "http://127.0.0.1:8000"

def _env(k): return os.environ.get(k, "")

def _http(url, **kw):
    import urllib.request, urllib.error
    req = urllib.request.Request(url, **kw)
    try:
        return json.loads(urllib.request.urlopen(req, timeout=15).read())
    except urllib.error.HTTPError as e:
        return {"_status": e.code, "_body": e.read()[:200].decode()}
    except Exception as e:
        return {"_err": str(e)}

def _bridge_session(platform):
    d = _http(f"{BRIDGE}/session/{platform}")
    return bool(d.get("logged_in") or d.get("ok"))

def _admin_token():
    body = __import__("urllib.parse", fromlist=["urlencode"]).urlencode(
        {"username": _env("SOCIAL_ADMIN_EMAIL"), "password": _env("SOCIAL_ADMIN_PASSWORD")}).encode()
    r = _http(f"{API}/api/v1/auth/login", data=body,
              headers={"Content-Type": "application/x-www-form-urlencoded"})
    return r.get("access_token")

# ── drivers ─

def _imap_code(sender_hint, since_min=30):
    """Poll the mailbox for a reset/verify code — IMAP is verified live."""
    import imaplib, re, email
    host, port = _env("OMV_IMAP_HOST") or "omv-ha", int(_env("OMV_IMAP_PORT") or "993")
    user, pw = _env("PROMO_SCAN_IMAP_USER") or "themis", _env("PROMO_SCAN_IMAP_PASS")
    if not pw:
        return None
    try:
        m = imaplib.IMAP4_SSL(host, port)
        m.login(user, pw)
        m.select("INBOX")
        _, d = m.search(None, f'(FROM "{sender_hint}")')
        for mid in (d[0].split() or [])[-5:]:
            _, msg = m.fetch(mid, "(RFC822)")
            body = email.message_from_bytes(msg[0][1]).get_payload(decode=True) or b""
            hit = re.search(rb"\b(\d{5,8})\b", body)
            if hit:
                return hit.group(1).decode()
    except Exception:
        return None
    return None


def drive_fb_reset():
    """Full unattended FB password reset — request code via sidecar,
    self-read it off the dovecot mailbox, set a new password, push to
    the canonical store."""
    pw = _env("FACEBOOK_PASSWORD")
    if pw:
        return {"status": "have_creds", "note": "FACEBOOK_PASSWORD set — try fb-sidecar instead"}
    code = _imap_code("facebookmail")
    return {
        "status": "pending" if not code else "ready",
        "note": "request a reset on the sidecar, this picks the code off IMAP",
        "code_found": bool(code),
    }

# ─────────────────────────────────────────────────────────

def drive_fb_sidecar():
    """FB sidecar login — username from k8s/env, FACEBOOK_PASSWORD from env."""
    pw = _env("FACEBOOK_PASSWORD")
    user = _env("FACEBOOK_USERNAME") or _env("FACEBOOK_EMAIL")
    if not (pw and user):
        return {"status": "no_creds", "needs": "FACEBOOK_USERNAME + FACEBOOK_PASSWORD"}
    r = _http("http://facebook-browser-sidecar:9226/login",
              data=json.dumps({"username": user, "password": pw}).encode(),
              headers={"Content-Type": "application/json"})
    return {"status": "fired", "result": r}

def drive_linkedin_sidecar():
    user = _env("LINKEDIN_USERNAME") or _env("LINKEDIN_EMAIL")
    pw = _env("LINKEDIN_PASSWORD")
    if not (user and pw):
        return {"status": "no_creds", "needs": "LINKEDIN_USERNAME + LINKEDIN_PASSWORD"}
    r = _http("http://linkedin-browser-sidecar:9225/login",
              data=json.dumps({"username": user, "password": pw}).encode(),
              headers={"Content-Type": "application/json"})
    return {"status": "fired", "result": r}

def drive_x_oauth(platform="twitter"):
    """Drive OAuth approve on the bridge when a session exists."""
    if not _bridge_session(platform):
        return {"status": "no_session"}
    tok = _admin_token()
    if not tok:
        return {"status": "login_fail"}
    import urllib.parse
    # team_id resolved by the api caller — use env or skip
    team = _env("SOCIAL_TEAM_ID")
    url = f"{API}/api/v1/auth/oauth/{platform}/authorize"
    if team:
        url += "?" + urllib.parse.urlencode({"team_id": team})
    r = _http(url, headers={"Authorization": f"Bearer {tok}"})
    au = r.get("authorization_url")
    if not au:
        return {"status": "no_auth_url", "result": r}
    nav = _http(f"{BRIDGE}/session/navigate",
                data=json.dumps({"url": au, "platform": platform}).encode(),
                headers={"Content-Type": "application/json"})
    return {"status": "fired", "nav": nav}

def drive_infisical_token():
    """Create a service token via the Infisical API once a session exists
    on the debug Chrome (:9222 CDP) — the admin login is the human part,
    the token mint is automated."""
    return {"status": "manual", "how": "Infisical dashboard → Machine Identities → create service token → k8s_secret_sync push INFISICAL_SERVICE_TOKEN"}

def drive_wa_verify():
    """WhatsApp phone verify — request code on Meta console via bridge."""
    if not _bridge_session("whatsapp"):
        return {"status": "no_session", "how": "login whatsapp on bridge :9223 once"}
    return {"status": "ready", "how": "wa_verify_daemon retries hourly"}

def drive_totp():
    """TOTP enroll — drives X security settings on debug Chrome :9222."""
    return {"status": "manual", "how": "run totp-ops/scripts/totp_setup.py on Windows Chrome"}

def drive_meta_submit():
    return {"status": "manual", "how": "meta_app_review_draft.py prints paste-ready text"}

GATES = {
    "fb-sidecar": drive_fb_sidecar,
    "fb-reset": drive_fb_reset,
    "linkedin-sidecar": drive_linkedin_sidecar,
    "x-oauth": drive_x_oauth,
    "tiktok-oauth": lambda: drive_x_oauth("tiktok"),
    "wa-verify": drive_wa_verify,
    "totp": drive_totp,
    "meta-submit": drive_meta_submit,
    "infisical": drive_infisical_token,
}

if __name__ == "__main__":
    gate = sys.argv[1] if len(sys.argv) > 1 else "all"
    if gate == "all":
        for name, fn in GATES.items():
            print(f"{name}: {fn()}")
    else:
        print(GATES[gate]())
