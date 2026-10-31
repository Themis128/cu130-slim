#!/usr/bin/env python3
"""ms_totp_enroll.py — enroll a TOTP authenticator for a Microsoft account
WITHOUT going through the broken Authenticator-app sign-in (AADSTS90023).

The sign-in paths fail; the TOTP-secret path works:
  phone Chrome -> account.live.com/proofs/manage/additional (signed in)
    -> "Use an app" -> "set up a different Authenticator app"
    -> "I can't scan the bar code" -> secret key on screen
  -> Authenticator -> + -> Other account -> Enter code manually
  -> secret stored in .env MS_TOTP_SECRET + authentik-secret slot on omv.

Usage: ms_totp_enroll.py [--device RFCTA0AEKZP] [--no-store]
"""
import argparse
import re
import sys
import time

sys.path.insert(0, "/mnt/c/Users/tbaltzakis/cu130-slim/"
                   ".devin/skills/phone-access/scripts")
import screen  # noqa: E402

SEC_URL = "https://account.live.com/proofs/manage/additional"
ENV_PATH = "/mnt/c/Users/tbaltzakis/cu130-slim/.env"
ENV_KEY = "MS_TOTP_SECRET"


def log(*a):
    print(*a, flush=True)


def capture_secret(dev):
    log("[1] open additional-security page (must be signed in)")
    screen.adb(dev, "shell", "am", "start", "-a",
               "android.intent.action.VIEW", "-d", SEC_URL,
               "com.android.chrome")
    if not screen.wait(dev, "Two-step verification", 40):
        log("  page not signed in — sign in once, then re-run")
        return None
    log("[2] 'Add a new way' -> 'Use an app'")
    screen.adb(dev, "shell", "input", "swipe", "540", "1800", "540", "800")
    hit = (screen.wait(dev, "Add another way", 15)
           or screen.wait(dev, "Add a new way", 15))
    if not hit:
        log("  no 'add' link found — 2SV may be off; enable it first")
        return None
    screen.tapxy(dev, hit[0], hit[1])
    if not screen.wait(dev, "Use an app", 15):
        log("  'Use an app' not found")
        return None
    hit = screen.find(dev, "Use an app")
    screen.tapxy(dev, hit[0], hit[1])
    log("[3] different Authenticator app -> can't scan -> secret")
    screen.wait(dev, "different Authenticator app", 12)
    screen.tap(dev, "set up a different Authenticator app")
    screen.wait(dev, "can't scan the bar code", 12)
    screen.tap(dev, "I can't scan the bar code")
    hit = screen.wait(dev, "Secret key:", 15)
    if not hit:
        log("  secret key not shown")
        return None
    m = re.search(r"Secret key:\s*([a-z0-9 ]+)", hit[2])
    if not m:
        return None
    return re.sub(r"\s", "", m.group(1))


def enroll_app(dev, secret, account):
    log("[4] Authenticator -> Other account -> enter code manually")
    screen.adb(dev, "shell", "am", "start", "-n",
               "com.azure.authenticator/.ui.LaunchActivity")
    time.sleep(4)
    if screen.find(dev, "Add account"):
        screen.tap(dev, "Add account")
    else:
        hit = screen.find(dev, "+")
        if hit:
            screen.tapxy(dev, *hit[:2])
    screen.wait(dev, "Other account", 8)
    screen.tap(dev, "Other account")
    if screen.wait(dev, "take pictures", 5):
        screen.tap(dev, "Only this time")
    screen.wait(dev, "Enter code manually", 8)
    screen.tap(dev, "Enter code manually")
    screen.wait(dev, "Secret Key", 8)
    screen.tapxy(dev, 540, 395)
    screen.type_text(dev, account)
    screen.tapxy(dev, 540, 644)
    screen.type_text(dev, secret)
    screen.tap(dev, "FINISH")
    if screen.wait(dev, "App Lock enabled", 6):
        screen.tap(dev, "OK")
    return True


def store(secret):
    line = f"{ENV_KEY}={secret}"
    txt = ""
    try:
        txt = open(ENV_PATH).read()
    except OSError:
        pass
    if ENV_KEY in txt:
        txt = re.sub(rf"^{ENV_KEY}=.*$", line, txt, flags=re.M)
    else:
        txt = txt.rstrip("\n") + "\n" + line + "\n"
    open(ENV_PATH, "w").write(txt)
    log("  .env", ENV_KEY, "stored")
    try:
        import base64
        import subprocess
        b64 = base64.b64encode(secret.encode()).decode()
        out = subprocess.run(
            ["ssh", "-i", "~/.ssh/id_rsa_win",
             "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10",
             "tbaltzakis@100.74.191.58",
             f"sudo kubectl patch secret authentik-secret -n authentik "
             f"--type merge -p '{{\"data\":{{\"ms-totp-secret\":\"{b64}\"}}}}'"],
            capture_output=True, text=True, timeout=40).stdout
        log("  authentik-secret slot:", "patched" if "patched" in out else out[:60])
    except Exception as e:
        log("  authentik slot skipped:", e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", "-s")
    ap.add_argument("--account", default="baltzakis.themis@gmail.com")
    ap.add_argument("--no-store", action="store_true")
    a = ap.parse_args()
    dev = a.device or screen.device()
    if not dev:
        sys.exit("no adb device")
    sec = capture_secret(dev)
    log("secret:", sec or "FAILED")
    if not sec:
        sys.exit(1)
    enroll_app(dev, sec, a.account)
    if not a.no_store:
        store(sec)
    log("DONE — verify with: totp_code.py MS")


if __name__ == "__main__":
    main()
