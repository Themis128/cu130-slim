#!/usr/bin/env python3
"""Watch the WhatsApp Business app coexistence link + Graph verification state.

Polls every POLL_S seconds for TIMEOUT_H hours:
  1. ADB uiautomator dump of the foreground WA Business app screen
     (detects transition away from "Unlock to link" -> QR/code/success).
  2. Graph API code_verification_status for phone_number_id
     1432734783254091 using META_ACCESS_TOKEN from ~/cu130-slim/.env.

Logs state transitions to ~/logs/wa-coexist-watch.log. Read-only —
never taps, types, or sends anything.
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
LOG = os.path.expanduser("~/logs/wa-coexist-watch.log")
PHONE_NUMBER_ID = "1432734783254091"
GRAPH = f"https://graph.facebook.com/v21.0/{PHONE_NUMBER_ID}"
POLL_S = 60
TIMEOUT_H = 24


def log(msg):
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}"
    print(line, flush=True)
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def token():
    for line in open(ENV):
        m = re.match(r"META_ACCESS_TOKEN=(.*)", line.strip())
        if m:
            return m.group(1).strip().strip('"')
    return None


def graph_status(tok):
    url = f"{GRAPH}?fields=code_verification_status,quality_rating&access_token={tok}"
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            d = json.loads(r.read())
            return d.get("code_verification_status"), d.get("quality_rating")
    except Exception as e:
        return f"ERR:{e}", None


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
    tok = token()
    log(f"wa-coexist-watch start serial={SERIAL} poll={POLL_S}s timeout={TIMEOUT_H}h token={'yes' if tok else 'NO'}")
    deadline = time.time() + TIMEOUT_H * 3600
    last_screen = last_graph = None
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
                    log("VERIFIED — coexistence/verification complete; safe to exit")
                    return
        time.sleep(POLL_S)
    log("timeout reached, exiting")


if __name__ == "__main__":
    main()
