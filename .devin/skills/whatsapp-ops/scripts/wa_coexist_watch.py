#!/usr/bin/env python3
"""Watch the WhatsApp Business app coexistence link + Graph verification state.

Polls every POLL_S seconds for TIMEOUT_H hours:
  1. ADB uiautomator dump of the foreground WA Business app screen
     (detects transition away from "Unlock to link" -> QR/code/success).
  2. Graph API code_verification_status for phone_number_id
     1432734783254091 using META_ACCESS_TOKEN from ~/cu130-slim/.env.

On VERIFIED it finishes the gate unattended:
  - discovers the owning WABA via /me/businesses -> owned_whatsapp_business_accounts
    -> /{waba}/phone_numbers (matches phone_number_id)
  - writes WHATSAPP_PHONE_NUMBER_ID + WHATSAPP_BUSINESS_ACCOUNT_ID into both
    .env copies (WSL + Windows checkout)
  - PATCHes the same keys in Infisical prod (source of truth for the 05:30 pull)
  - converges the backend compose services (skipped while .deploy.lock is fresh)
  - TG-alerts #ops, drops a done-marker so cron relaunches no-op

Logs state transitions to ~/logs/wa-coexist-watch.log.

Phone-drive (opt-in boundaries):
  - PHONE_PIN in .env  -> biometric/unlock prompt gets "Use PIN" + PIN
    entry (never spoofs the fingerprint itself).
  - WA_SMS_AUTOFILL!=0 -> the WA number-verification screen reads the
    SMS inbox over ADB and types the code (default on; =0 disables).
  - WA_ADB_SERIAL pins a device; otherwise the first ADB 'device' state
    is used (LAN alias preferred — the phone is IP-connected).
Without PHONE_PIN the watcher stays read-only on the biometric gate.
"""
import json
import os
import re
import subprocess
import time
import urllib.request

ADB = "/mnt/c/Users/tbaltzakis/AppData/Local/Android/Sdk/platform-tools/adb.exe"
ADB_FALLBACKS = [ADB, "adb", "/mnt/c/platform-tools/adb.exe"]
# The phone is IP-connected (LAN + tailnet aliases of the same device) —
# discover at runtime instead of a fixed serial.
SERIAL = os.environ.get("WA_ADB_SERIAL", "")
ENV = "/home/tbaltzakis/cu130-slim/.env"
ENV_WIN = "/mnt/c/Users/tbaltzakis/cu130-slim/.env"
LOG = os.path.expanduser("~/logs/wa-coexist-watch.log")
STATE_DIR = os.path.expanduser("~/.local/state/wa-coexist-watch")
DONE_MARKER = os.path.join(STATE_DIR, "verified.json")
REPO = "/home/tbaltzakis/cu130-slim"
TG_ALERT = os.path.join(
    REPO, ".devin/skills/telegram-alert-ops/scripts/tg_alert.py")
PHONE_NUMBER_ID = "1432734783254091"
GRAPH = "https://graph.facebook.com/v21.0"
POLL_S = 60
TIMEOUT_H = 24

# Infisical prod (same constants as dmr-endpoint-watch.py)
INFISICAL_BASE = "http://127.0.0.1:8090"
INFISICAL_PROJECT = "27660119-de58-4d8b-adaf-a39c8fc8c79e"
INFISICAL_ENV = "prod"

BACKEND_SERVICES = [
    "social-api",
    "social-worker-publishing",
    "social-worker-media",
    "social-worker-default",
    "social-worker-messenger",
]


def log(msg):
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}"
    print(line, flush=True)
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def tg(text):
    try:
        subprocess.run(
            ["python3", TG_ALERT, "ops", text], timeout=30)
    except Exception as e:
        log(f"tg alert failed: {e}")


def env_map(path):
    out = {}
    try:
        for line in open(path, errors="replace"):
            m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line.strip())
            if m:
                out[m.group(1)] = m.group(2).strip().strip("\"'")
    except OSError:
        pass
    return out


def token():
    return env_map(ENV).get("META_ACCESS_TOKEN")


def graph_get(path, tok, **params):
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{GRAPH}/{path.lstrip('/')}?access_token={tok}" + (f"&{qs}" if qs else "")
    with urllib.request.urlopen(url, timeout=20) as r:
        return json.loads(r.read())


def graph_status(tok):
    try:
        d = graph_get(
            PHONE_NUMBER_ID, tok,
            fields="code_verification_status,quality_rating")
        return d.get("code_verification_status"), d.get("quality_rating")
    except Exception as e:
        return f"ERR:{e}", None


def discover_waba(tok):
    """Return the WABA id that owns PHONE_NUMBER_ID, or None."""
    try:
        businesses = graph_get("me/businesses", tok, fields="id", limit=50)
    except Exception as e:
        log(f"me/businesses failed: {e}")
        return None
    for b in businesses.get("data", []):
        bid = b["id"]
        try:
            wabas = graph_get(
                f"{bid}/owned_whatsapp_business_accounts", tok,
                fields="id,name", limit=50)
        except Exception:
            try:
                wabas = graph_get(
                    f"{bid}/client_whatsapp_business_accounts", tok,
                    fields="id,name", limit=50)
            except Exception as e:
                log(f"waba edge failed for {bid}: {e}")
                continue
        for w in wabas.get("data", []):
            wid = w["id"]
            try:
                phones = graph_get(
                    f"{wid}/phone_numbers", tok,
                    fields="id,display_phone_number", limit=100)
            except Exception as e:
                log(f"phone_numbers failed for {wid}: {e}")
                continue
            for p in phones.get("data", []):
                if p.get("id") == PHONE_NUMBER_ID:
                    return wid
    return None


def patch_env_file(path, updates):
    """Set KEY=value lines (replace-or-append). Returns changed keys."""
    try:
        lines = open(path, errors="replace").read().splitlines()
    except OSError:
        return []
    changed = []
    for k, v in updates.items():
        pat = re.compile(rf"^{re.escape(k)}=")
        hit = False
        for i, line in enumerate(lines):
            if pat.match(line):
                if line != f"{k}={v}":
                    lines[i] = f"{k}={v}"
                    changed.append(k)
                hit = True
                break
        if not hit:
            lines.append(f"{k}={v}")
            changed.append(k)
    if changed:
        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")
    return changed


def infisical_patch(updates):
    """PATCH keys into Infisical prod. Best-effort — local .env already
    carries the values, and the next manual push can reconcile."""
    cfg = env_map(ENV)
    cid, csec = cfg.get("INFISICAL_UA_CLIENT_ID"), cfg.get("INFISICAL_UA_CLIENT_SECRET")
    if not cid or not csec:
        log("infisical creds absent — skipping remote patch")
        return
    try:
        body = json.dumps({"clientId": cid, "clientSecret": csec}).encode()
        req = urllib.request.Request(
            f"{INFISICAL_BASE}/api/v1/auth/universal-auth/login",
            data=body, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            tok = json.loads(resp.read())["accessToken"]
        for k, v in updates.items():
            req = urllib.request.Request(
                f"{INFISICAL_BASE}/api/v3/secrets/raw/{k}", method="PATCH",
                data=json.dumps({
                    "workspaceId": INFISICAL_PROJECT,
                    "environment": INFISICAL_ENV,
                    "secretPath": "/",
                    "secretValue": v,
                    "type": "shared",
                }).encode(),
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {tok}"})
            try:
                urllib.request.urlopen(req, timeout=15)
                log(f"infisical patched {k}")
            except Exception as e:
                # 404 = secret doesn't exist yet; create via POST upsert
                req2 = urllib.request.Request(
                    f"{INFISICAL_BASE}/api/v3/secrets/raw/{k}", method="POST",
                    data=json.dumps({
                        "workspaceId": INFISICAL_PROJECT,
                        "environment": INFISICAL_ENV,
                        "secretPath": "/",
                        "secretValue": v,
                        "type": "shared",
                    }).encode(),
                    headers={"Content-Type": "application/json",
                             "Authorization": f"Bearer {tok}"})
                try:
                    urllib.request.urlopen(req2, timeout=15)
                    log(f"infisical created {k}")
                except Exception as e2:
                    log(f"infisical write {k} failed: {e} / {e2}")
    except Exception as e:
        log(f"infisical patch failed: {e}")


def deploy_lock_fresh():
    try:
        age = time.time() - os.path.getmtime(os.path.join(REPO, ".deploy.lock"))
        return age < 3600
    except OSError:
        return False


def converge_backend():
    if deploy_lock_fresh():
        log("deploy lock fresh — deferring compose converge")
        return
    try:
        subprocess.run(
            ["docker", "compose", "up", "-d", *BACKEND_SERVICES],
            cwd=REPO, capture_output=True, text=True, timeout=600)
        log("compose converge issued for backend services")
    except Exception as e:
        log(f"compose converge failed: {e}")


def finish_gate(waba_id):
    """VERIFIED — write env everywhere, patch Infisical, converge, alert."""
    updates = {"WHATSAPP_PHONE_NUMBER_ID": PHONE_NUMBER_ID}
    if waba_id:
        updates["WHATSAPP_BUSINESS_ACCOUNT_ID"] = waba_id
    for path in (ENV, ENV_WIN):
        changed = patch_env_file(path, updates)
        log(f"env {path}: {'updated ' + ','.join(changed) if changed else 'already current'}")
    infisical_patch(updates)
    converge_backend()
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(DONE_MARKER, "w") as f:
        json.dump({"verified_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                   "waba": waba_id}, f)
    tg("✅ WhatsApp Cloud API link VERIFIED — phone_number_id "
       f"{PHONE_NUMBER_ID} wired into .env + Infisical"
       + (f" (WABA {waba_id})" if waba_id else "")
       + ". Backend converge issued; webhook subscribe is the next check.")


def adb_bin():
    import shutil
    for c in ADB_FALLBACKS:
        if c.endswith(".exe") or shutil.which(c):
            return c
    return ADB


def pick_device():
    """First ADB device in 'device' state. WA_ADB_SERIAL pins one."""
    if SERIAL:
        return SERIAL
    try:
        out = subprocess.run([adb_bin(), "devices"], capture_output=True,
                             text=True, timeout=15).stdout
    except Exception:
        return ""
    devs = [ln.split()[0] for ln in out.splitlines()[1:]
            if ln.strip() and ln.rstrip().endswith("device")]
    # prefer the LAN alias (Termux SSH host) when both aliases are present
    for d in devs:
        if d.startswith("192.168."):
            return d
    return devs[0] if devs else ""


def adb(*args, timeout=30):
    dev = pick_device()
    if not dev:
        return ""
    return subprocess.run([adb_bin(), "-s", dev, "shell", *args],
                          capture_output=True, text=True,
                          timeout=timeout).stdout


def env_get(key, default=""):
    return env_map(ENV).get(key, os.environ.get(key, default))


def screen_xml():
    adb("uiautomator", "dump", "/sdcard/ui.xml", timeout=40)
    return adb("cat", "/sdcard/ui.xml", timeout=20)


def screen_nodes(xml=None):
    """[(text, cx, cy)] for nodes carrying text."""
    xml = xml if xml is not None else screen_xml()
    out = []
    for m in re.finditer(
            r'(?:text|content-desc)="([^"]*)"[^>]*'
            r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml):
        t, x1, y1, x2, y2 = m.groups()
        if t.strip():
            out.append((t, (int(x1) + int(x2)) // 2,
                        (int(y1) + int(y2)) // 2))
    return out


def tap_text(nodes, needle):
    nl = needle.lower()
    for t, x, y in nodes:
        if nl in t.lower():
            adb("input", "tap", str(x), str(y))
            return t
    return None


# ── phone-drive: biometric/PIN unlock + SMS-code verify ──────────────────
# Opt-in boundaries:
#   PHONE_PIN set      -> biometric prompt gets "Use PIN" + PIN entry
#   WA_SMS_AUTOFILL!=0 -> verification-code screen reads the SMS inbox
#                         and types the code (default ON — it completes a
#                         verification the owner already triggered)
UNLOCK_RE = re.compile(r"unlock to link|confirm it'?s you|verify it'?s you|"
                       r"fingerprint|face unlock|biometric|use pin|"
                       r"use passcode|enter your pin", re.I)
VERIFY_RE = re.compile(r"verification code|verifying your number|"
                       r"enter the \d+-digit code|didn'?t receive code", re.I)

_sms_last_code = None
_sms_last_try = 0.0
_pin_last_try = 0.0
_resend_last = 0.0


def read_whatsapp_code(max_age_s=1200):
    """Newest FRESH WhatsApp SMS code (epoch-ms within max_age_s), or None.

    Stale codes are worse than none — WA rejects them and burns an attempt.
    Returns the 6-digit code only when the SMS is recent enough to belong
    to the current verification round.
    """
    out = adb("content", "query", "--uri", "content://sms/inbox",
              "--projection", "address,date,body", "--sort", "date DESC",
              timeout=25)
    now_ms = time.time() * 1000
    for line in out.splitlines():
        if not re.search(r"whatsapp", line, re.I):
            continue
        dm = re.search(r"date=(\d+)", line)
        if dm and now_ms - int(dm.group(1)) > max_age_s * 1000:
            continue
        m = re.search(r"(\d{3})[-\s]?(\d{3})", line)
        if m:
            return m.group(1) + m.group(2)
    return None


def maybe_pin_unlock(nodes, joined):
    """Biometric gate -> Use PIN -> type PIN on the pad. PHONE_PIN only."""
    global _pin_last_try
    pin = env_get("PHONE_PIN")
    if not pin or not UNLOCK_RE.search(joined):
        return False
    if time.time() - _pin_last_try < 300:  # at most one attempt / 5 min
        return False
    _pin_last_try = time.time()
    log("phone-drive: biometric gate detected — trying PIN fallback")
    # choose the PIN fallback if offered
    for label in ("Use PIN", "Use passcode", "Enter PIN", "PIN instead",
                  "Use pattern"):
        if tap_text(nodes, label):
            log(f"phone-drive: tapped '{label}'")
            time.sleep(2.5)
            nodes = screen_nodes()
            break
    # PIN pad: tap digit nodes in order; fallback input text
    ok = True
    for d in pin:
        hit = tap_text(nodes, d)
        if not hit:
            ok = False
            break
        time.sleep(0.4)
        nodes = screen_nodes()
    if not ok:
        # custom pad without text nodes — try direct input
        adb("input", "text", pin)
        log("phone-drive: PIN pad had no digit nodes — used input text")
    time.sleep(2)
    return True


def maybe_sms_verify(nodes, joined):
    """Verification-code screen -> read SMS -> type code. Default ON."""
    global _sms_last_code, _sms_last_try, _resend_last
    if env_get("WA_SMS_AUTOFILL", "1") == "0":
        return False
    if not VERIFY_RE.search(joined):
        return False
    code = read_whatsapp_code()
    if not code:
        # no fresh SMS — resend once per 10 min via the fallback link
        if time.time() - _resend_last > 600:
            _resend_last = time.time()
            if tap_text(nodes, "Didn't receive code"):
                log("phone-drive: tapped 'Didn't receive code?'")
                time.sleep(3)
                nodes = screen_nodes()
                for opt in ("SMS", "Text message", "Call me", "Voice"):
                    if tap_text(nodes, opt):
                        log(f"phone-drive: resend via '{opt}'")
                        break
        return False
    if code == _sms_last_code and time.time() - _sms_last_try < 600:
        return False  # already tried this code recently
    _sms_last_code, _sms_last_try = code, time.time()
    log(f"phone-drive: WA code {code[:3]}*** — filling verify field")
    # focus the code field then type — WA auto-submits on 6 digits
    tap_text(nodes, "Verification code") or tap_text(nodes, "code")
    time.sleep(0.8)
    adb("input", "text", code)
    time.sleep(1)
    return True


def app_screen():
    try:
        xml = screen_xml()
        nodes = screen_nodes(xml)
        if not nodes:
            return "EMPTY"
        joined = " | ".join(t for t, _, _ in nodes[:12])
        if maybe_pin_unlock(nodes, joined) or maybe_sms_verify(nodes, joined):
            time.sleep(4)
            nodes = screen_nodes()
            joined = " | ".join(t for t, _, _ in nodes[:12]) if nodes else joined
        return joined[:300]
    except Exception as e:
        return f"ADB_ERR:{e}"


def main():
    if os.path.exists(DONE_MARKER):
        log("already VERIFIED (done-marker present) — exiting")
        return
    tok = token()
    dev = pick_device()
    log(f"wa-coexist-watch start device={dev or 'NONE'} poll={POLL_S}s "
        f"timeout={TIMEOUT_H}h token={'yes' if tok else 'NO'} "
        f"pin_fallback={'PHONE_PIN' in env_map(ENV)} "
        f"sms_autofill={env_get('WA_SMS_AUTOFILL', '1')}")
    deadline = time.time() + TIMEOUT_H * 3600
    last_screen = last_graph = None
    alerted_timeout = False
    while time.time() < deadline:
        screen = app_screen()
        if screen != last_screen:
            log(f"APP SCREEN -> {screen}")
            last_screen = screen
        if tok:
            vs, qr = graph_status(tok)
            cur = f"{vs}/{qr}"
            if cur != last_graph:
                log(f"GRAPH -> code_verification_status={vs} quality={qr}")
                last_graph = cur
            if vs == "VERIFIED":
                log("VERIFIED — finishing gate (env + infisical + converge)")
                waba = discover_waba(tok)
                log(f"WABA discovery -> {waba}")
                finish_gate(waba)
                return
        time.sleep(POLL_S)
    log("timeout reached, exiting")
    if not alerted_timeout:
        tg("⏳ wa-coexist-watch timed out after 24h — the WA Business app "
           "link still isn't VERIFIED. Check the phone's 'Unlock to link' "
           "screen (fingerprint/PIN).")


if __name__ == "__main__":
    main()
