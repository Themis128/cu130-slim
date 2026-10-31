#!/usr/bin/env python3
"""fb_pw_reset.py — drive the Facebook "Forgot password" flow end-to-end.

Sidecar (facebook-browser-sidecar :9226) navigates the recovery flow:
  identify account -> code requested -> code self-reads off the phone
  (wa_verify lanes: ADB / Termux-SSH / mail-forward) or Gmail -> set new
  password -> FB_PASSWORD written to .env.

Usage:
  fb_pw_reset.py <new-password>          # full drive
  fb_pw_reset.py <new-password> --dry    # print steps, don't drive
"""
import argparse
import json
import os
import re
import subprocess
import time
import urllib.request

SIDECAR = "http://localhost:9226"
ENV_PATH = r"C:\Users\tbaltzakis\cu130-slim\.env"
GMAIL_SECRETS = os.path.expandvars(
    r"%APPDATA%\devin\secrets\gmail-token.json")
GMAIL_KEYS = os.path.expandvars(
    r"%APPDATA%\devin\secrets\gcp-oauth.keys.json")
KEY = os.path.expanduser("~/.ssh/id_rsa_win")
PHONE_IP = os.environ.get("PHONE_ADB_IP", "192.168.1.77")
ADBS = ["adb",
        "/mnt/c/Users/tbaltzakis/AppData/Local/Android/Sdk/platform-tools/adb.exe",
        "/mnt/c/platform-tools/adb.exe"]
CODE_RE = re.compile(r"\b(\d{6})\b")


def req(method, path, payload=None, timeout=60):
    r = urllib.request.Request(
        SIDECAR + path,
        data=json.dumps(payload).encode() if payload else None,
        headers={"Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(r, timeout=timeout) as f:
        return json.loads(f.read())


def ev(script, timeout=30):
    return req("POST", "/debug/eval", {"script": script}, timeout).get("result")


def page():
    return req("GET", "/debug/page-text", timeout=20).get("text", "")


def click(text):
    return ev(
        "(()=>{const b=[...document.querySelectorAll('button,a,div[role=button],span')]"
        f".find(e=>(e.innerText||'').trim().toLowerCase()==='{text.lower()}');"
        "if(b){b.click();return 'clicked'}return 'not-found'})()")


def fill(sel_value):
    return ev(
        "(()=>{const i=document.querySelector('input[type=password],input[type=text],input[type=email]');"
        f"if(i){{i.focus();i.value='{sel_value}';i.dispatchEvent(new Event('input',{{bubbles:true}}));"
        "return 'filled'}return 'no-field'})()")


def adb_code():
    for b in ADBS:
        try:
            r = subprocess.run(
                [b, "shell", "content", "query", "--uri", "content://sms/inbox",
                 "--projection", "address,date,body", "--sort", "date DESC"],
                capture_output=True, text=True, timeout=8)
            rows = [row for row in r.stdout.splitlines() if re.search(
                r'facebook|meta|AUTHMSG', row, re.I)]
            m = CODE_RE.search(" ".join(rows)) if rows else CODE_RE.search(r.stdout)
            if m:
                return m.group(1)
        except Exception:
            continue
    return None


def termux_code():
    try:
        r = subprocess.run(
            ["ssh", "-p", "8022", "-o", "ConnectTimeout=8",
             "-o", "StrictHostKeyChecking=no", "-i", KEY,
             f"u0_a357@{PHONE_IP}", "termux-sms-list -l 5"],
            capture_output=True, text=True, timeout=12)
        rows = [row for row in r.stdout.splitlines() if re.search(
            r'facebook|meta|AUTHMSG', row, re.I)]
        m = CODE_RE.search(" ".join(rows)) if rows else CODE_RE.search(r.stdout)
        return m.group(1) if m else None
    except Exception:
        return None


def gmail_code():
    """FB recovery codes land in Gmail — decode newest security@ mail."""
    try:
        tok = json.load(open(GMAIL_SECRETS))
        keys = json.load(open(GMAIL_KEYS))['installed']
        r = urllib.request.urlopen(urllib.request.Request(
            "https://oauth2.googleapis.com/token",
            data=urllib.parse.urlencode({
                "client_id": keys["client_id"],
                "client_secret": keys["client_secret"],
                "refresh_token": tok["refresh_token"],
                "grant_type": "refresh_token"}).encode()))
        at = json.loads(r.read())["access_token"]
        r = urllib.request.urlopen(urllib.request.Request(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages"
            "?q=from:security@facebookmail.com+newer_than:10m&maxResults=2",
            headers={"Authorization": "Bearer " + at}))
        ids = [m["id"] for m in json.loads(r.read()).get("messages", [])]
        for mid in ids:
            r = urllib.request.urlopen(urllib.request.Request(
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{mid}?format=full",
                headers={"Authorization": "Bearer " + at}))
            body = json.dumps(json.loads(r.read()))
            m = CODE_RE.search(body)
            if m:
                return m.group(1)
    except Exception as e:
        print("gmail err:", e)
    return None


def any_code():
    return adb_code() or termux_code() or gmail_code()


def save_pw(pw):
    # .env append/update — FB_PASSWORD slot
    if os.path.exists(ENV_PATH):
        lines = open(ENV_PATH).read().split("\n")
        out, seen = [], False
        for ln in lines:
            if ln.startswith("FB_PASSWORD="):
                out.append(f"FB_PASSWORD={pw}")
                seen = True
            else:
                out.append(ln)
        if not seen:
            out.append(f"FB_PASSWORD={pw}")
        open(ENV_PATH, "w").write("\n".join(out))
        print("FB_PASSWORD written to .env")


def reset(new_pw, dry=False):
    """Navigate forgot-password -> identify -> code -> new password."""
    print("[1] navigate to recovery")
    if not dry:
        req("POST", "/debug/navigate",
            {"url": "https://www.facebook.com/login/identify/"})
        time.sleep(8)
    print("  page:", page()[:100] if not dry else "(dry)")

    print("[2] identify account — tap Search/Continue after entering id")
    if not dry:
        fill("tbaltzakis")  # or email — the identify page takes either
        click("Search") or click("Continue")
        time.sleep(6)

    print("[3] request the code")
    if not dry:
        click("Send code") or click("Continue") or click("Get code")
        time.sleep(8)
        print("  code-requested state:", page()[:100])

    print("[4] poll the code off the phone / gmail (90s)")
    code = None
    if not dry:
        for _ in range(9):
            code = any_code()
            if code:
                break
            time.sleep(10)
        print("  code:", code or "NONE — enter manually")

    print("[5] enter code + new password")
    if not dry and code:
        fill(code)
        click("Continue")
        time.sleep(6)
        fill(new_pw)
        click("Continue")
        time.sleep(6)
        print("  post-reset page:", page()[:100])
        save_pw(new_pw)
        print("done — FB password reset to new value + stored")
    elif not dry:
        print("no code — drive manually from the recovery page")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("password")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    reset(a.password, a.dry)
