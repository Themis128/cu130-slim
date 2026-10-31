#!/usr/bin/env python3
"""ms_pw_reset.py — drive a Microsoft account password reset end-to-end.

Sidecar navigates account.live.com recovery: identify -> verify contact
-> code requested -> code self-reads (ADB SMS / Termux-SSH / Gmail
accountprotection.microsoft.com) -> set new password -> stored.

Usage:
  ms_pw_reset.py <email> <new-password>
  ms_pw_reset.py <email> <new-password> --dry
"""
import argparse
import json
import os
import re
import subprocess
import time
import urllib.parse
import urllib.request

SIDECAR = "http://localhost:9226"
GMAIL_SECRETS = os.path.expandvars(
    r"%APPDATA%\devin\secrets\gmail-token.json")
GMAIL_KEYS = os.path.expandvars(
    r"%APPDATA%\devin\secrets\gcp-oauth.keys.json")
KEY = os.path.expanduser("~/.ssh/id_rsa_win")
PHONE_IP = os.environ.get("PHONE_ADB_IP", "192.168.1.77")
ADBS = ["adb",
        "/mnt/c/Users/tbaltzakis/AppData/Local/Android/Sdk/platform-tools/adb.exe",
        "/mnt/c/platform-tools/adb.exe"]
CODE_RE = re.compile(r"\b(\d{6,7})\b")
MS_CODE_RE = re.compile(r"security code\D{0,20}(\d{6,7})|(\d{6,7})\s*is your",
                        re.I)


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
        "(()=>{const b=[...document.querySelectorAll('button,a,input[type=submit],div[role=button]')]"
        f".find(e=>((e.innerText||e.value||'').trim().toLowerCase().includes('{text.lower()}')))"
        ";if(b){b.click();return 'clicked'}return 'not-found'})()")


def fill(value):
    return ev(
        "(()=>{const i=document.querySelector('input[type=text],input[type=email],"
        "input[type=tel],input[type=password],input:not([type])');"
        f"if(i){{i.focus();i.value='{value}';i.dispatchEvent(new Event('input',{{bubbles:true}}));"
        "return 'filled'}return 'no-field'})()")


def adb_code():
    for b in ADBS:
        try:
            r = subprocess.run(
                [b, "shell", "content", "query", "--uri", "content://sms/inbox",
                 "--projection", "address,date,body", "--sort", "date DESC"],
                capture_output=True, text=True, timeout=8)
            rows = [l for l in r.stdout.splitlines() if re.search(
                r'microsoft|accountprotection|AUTHMSG', l, re.I)]
            m = CODE_RE.search(" ".join(rows)) if rows else None
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
        rows = [l for l in r.stdout.splitlines() if re.search(
            r'microsoft|accountprotection|AUTHMSG', l, re.I)]
        m = CODE_RE.search(" ".join(rows)) if rows else None
        return m.group(1) if m else None
    except Exception:
        return None


def gmail_code():
    """MS recovery codes come from accountprotection.microsoft.com."""
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
            "?q=from:accountprotection.microsoft.com+newer_than:10m&maxResults=2",
            headers={"Authorization": "Bearer " + at}))
        ids = [m["id"] for m in json.loads(r.read()).get("messages", [])]
        for mid in ids:
            r = urllib.request.urlopen(urllib.request.Request(
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{mid}?format=full",
                headers={"Authorization": "Bearer " + at}))
            body = json.dumps(json.loads(r.read()))
            m = MS_CODE_RE.search(body) or CODE_RE.search(body)
            if m:
                return next(g for g in m.groups() if g)
    except Exception as e:
        print("gmail err:", e)
    return None


def any_code():
    return adb_code() or termux_code() or gmail_code()


def reset(email, new_pw, dry=False):
    print("[1] account.live.com recovery")
    if not dry:
        req("POST", "/debug/navigate",
            {"url": "https://account.live.com/password/reset"})
        time.sleep(8)
    print("  page:", page()[:120] if not dry else "(dry)")

    print("[2] enter account email")
    if not dry:
        fill(email)
        click("Next") or click("Continue")
        time.sleep(6)

    print("[3] choose verification contact (email/phone shown masked)")
    if not dry:
        print("  contact options:", page()[:150])
        click("Get code") or click("Send code") or click("Next")
        time.sleep(6)

    print("[4] poll the code — sms/gmail lanes (90s)")
    code = None
    if not dry:
        for _ in range(9):
            code = any_code()
            if code:
                break
            time.sleep(10)
        print("  code:", code or "NONE — enter manually")

    print("[5] enter code -> new password")
    if not dry and code:
        fill(code)
        click("Next") or click("Continue")
        time.sleep(6)
        fill(new_pw)
        click("Next") or click("Save") or click("Continue")
        time.sleep(6)
        print("  post-reset page:", page()[:120])
        print("done — MS password reset")
    elif not dry:
        print("no code — drive manually")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("email")
    ap.add_argument("password")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    reset(a.email, a.password, a.dry)
