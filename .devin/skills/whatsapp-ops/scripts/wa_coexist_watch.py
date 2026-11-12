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

Logs state transitions to ~/logs/wa-coexist-watch.log. Never taps, types,
or sends anything on the phone — the only human step is the fingerprint/PIN
unlock that lets the app finish the link.
"""
import json
import os
import re
import subprocess
import time
import urllib.request

ADB = "/mnt/c/Users/tbaltzakis/AppData/Local/Android/Sdk/platform-tools/adb.exe"
SERIAL = "RFCTA0AEKZP"
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


def app_screen():
    try:
        subprocess.run(
            [ADB, "-s", SERIAL, "shell", "uiautomator", "dump", "/sdcard/ui.xml"],
            capture_output=True, timeout=30,
        )
        out = subprocess.run(
            [ADB, "-s", SERIAL, "shell", "cat", "/sdcard/ui.xml"],
            capture_output=True, timeout=30,
        ).stdout.decode("utf-8", "replace")
    except Exception as e:
        return f"ADB_ERR:{e}"
    texts = re.findall(r'text="([^"]+)"', out)
    texts = [t for t in texts if t.strip()]
    if not texts:
        return "EMPTY"
    return " | ".join(texts[:8])[:300]


def main():
    if os.path.exists(DONE_MARKER):
        log("already VERIFIED (done-marker present) — exiting")
        return
    tok = token()
    log(f"wa-coexist-watch start serial={SERIAL} poll={POLL_S}s "
        f"timeout={TIMEOUT_H}h token={'yes' if tok else 'NO'}")
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
