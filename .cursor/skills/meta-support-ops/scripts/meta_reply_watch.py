#!/usr/bin/env python3
"""meta_reply_watch.py — watchdog for Meta/Facebook support + security mail.

Polls baltzakis.themis@gmail.com via the Gmail API (same OAuth token as
gmail_code_fetch.py, read from the Windows secrets dir via /mnt/c) for new
messages from Meta domains and fires a Telegram alert for the classes that
matter:

  SUPPORT  — case updates, "we received your request", appeal decisions,
             business-support replies  -> sev ops
  SECURITY — unrecognized-login / "Was this you" / password-changed alerts
             -> sev critical (possible takeover signal)
  CODES    — 2FA/login confirmation codes -> logged only, no alert
             (they're consumed by the login flows, not news)

State: ~/logs/meta-reply-watch.state.json (seen message ids, capped).
Usage: meta_reply_watch.py [--once] [--dry] [--hours N]
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

SECRETS = "/mnt/c/Users/tbaltzakis/AppData/Roaming/devin/secrets"
TOKEN_PATH = os.path.join(SECRETS, "gmail-token.json")
KEYS_PATH = os.path.join(SECRETS, "gcp-oauth.keys.json")
STATE = os.path.expanduser("~/logs/meta-reply-watch.state.json")
TG_ALERT = os.path.expanduser(
    "~/cu130-slim/.devin/skills/telegram-alert-ops/scripts/tg_alert.py")
HOURS = 48
MAX_SEEN = 500

META_FROM = '(from:facebookmail.com OR from:meta.com OR from:fb.com ' \
            'OR from:facebook.com OR from:instagram.com)'

CODE_RX = re.compile(r"confirmation code|security code|is your (login )?code|"
                     r"\d{6,8} is your|one-time|verification code",
                     re.I)
SEC_RX = re.compile(r"unrecognized|was this you|new login|password (was )?"
                    r"changed|someone (else )?logged|suspicious|"
                    r"reset your password|suspended for your protection",
                    re.I)
RECOVERY_RX = re.compile(r"get back (into|on)|log in without password|"
                         r"use \d+ to log", re.I)
SUPPORT_RX = re.compile(r"case|appeal|report|request|support|"
                        r"we('ve| have) received|response|decision|"
                        r"violation|restriction|unblock", re.I)


def access_token():
    tok = json.load(open(TOKEN_PATH))
    keys = json.load(open(KEYS_PATH))
    c = keys.get("installed", keys.get("web", {}))
    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=urllib.parse.urlencode({
            "client_id": c["client_id"],
            "client_secret": c["client_secret"],
            "refresh_token": tok["refresh_token"],
            "grant_type": "refresh_token"}).encode())
    r = json.load(urllib.request.urlopen(req, timeout=15))
    tok["access_token"] = r["access_token"]
    json.dump(tok, open(TOKEN_PATH, "w"))
    return r["access_token"]


def gmail_get(at, path):
    req = urllib.request.Request(
        "https://gmail.googleapis.com/gmail/v1/users/me/" + path,
        headers={"Authorization": f"Bearer {at}"})
    return json.load(urllib.request.urlopen(req, timeout=20))


def load_state():
    try:
        return json.load(open(STATE))
    except Exception:
        return {"seen": []}


def save_state(st):
    st["seen"] = st["seen"][-MAX_SEEN:]
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(st, open(STATE, "w"))


def classify(subject, snippet):
    s = f"{subject} {snippet}"
    if CODE_RX.search(s) and not (SUPPORT_RX.search(s)
                                  or RECOVERY_RX.search(s)):
        return "code"
    if SEC_RX.search(s):
        return "security"
    if RECOVERY_RX.search(s):
        return "recovery"
    if SUPPORT_RX.search(s):
        return "support"
    return "other"


def alert(sev, text):
    subprocess = __import__("subprocess")
    subprocess.run(["python3", TG_ALERT, sev, text],
                   capture_output=True, timeout=30)


def main():
    hours = HOURS
    once = "--once" in sys.argv
    dry = "--dry" in sys.argv
    seed = "--seed" in sys.argv  # mark current mail seen, alert nothing
    for a in sys.argv[1:]:
        if a.startswith("--hours="):
            hours = int(a.split("=", 1)[1])
    st = load_state()
    seen = set(st["seen"])
    at = access_token()
    q = f"{META_FROM} newer_than:{hours}h"
    res = gmail_get(at, "messages?" + urllib.parse.urlencode(
        {"q": q, "maxResults": 50}))
    msgs = res.get("messages", [])
    new = []
    for m in msgs:
        if m["id"] in seen:
            continue
        d = gmail_get(at, f"messages/{m['id']}?format=metadata"
                      "&metadataHeaders=Subject&metadataHeaders=From"
                      "&metadataHeaders=Date")
        hdrs = {h["name"].lower(): h["value"]
                for h in d["payload"]["headers"]}
        subj = hdrs.get("subject", "(no subject)")
        frm = hdrs.get("from", "?")
        snip = (d.get("snippet") or "")[:160]
        cls = classify(subj, snip)
        new.append({"id": m["id"], "cls": cls, "from": frm,
                    "subject": subj, "snippet": snip,
                    "date": hdrs.get("date", "")})
        seen.add(m["id"])
    st["seen"] = list(seen)
    if not dry:
        save_state(st)
    counts = {}
    for n in new:
        counts[n["cls"]] = counts.get(n["cls"], 0) + 1
        if n["cls"] == "code":
            print(f"[code] {n['subject'][:70]}")
            continue
        sev = ("critical" if n["cls"] == "security"
               else "ops" if n["cls"] in ("support", "recovery")
               else "digest")
        line = (f"[meta-reply/{n['cls']}] {n['subject'][:110]}\n"
                f"from: {n['from'][:60]}\n{n['snippet']}")
        print(line)
        if not dry and not seed and n["cls"] != "other":
            alert(sev, f"Meta mail watchdog: {line[:350]}")
    print(f"scanned={len(msgs)} new={len(new)} "
          f"classes={counts} state_saved={not dry}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
