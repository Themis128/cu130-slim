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


CHECKPOINT_LINK_RX = re.compile(
    r"https://(?:auth\.meta\.com|www\.facebook\.com|facebook\.com|"
    r"(?:www\.)?instagram\.com)/[^\s\"'<>]*?(?:checkpoint|checkpoints|"
    r"recover|hacked|disabled)[^\s\"'<>]*", re.I)
CDP_URL = os.environ.get("META_CDP_URL", "http://192.168.96.1:9229")


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


def _b64url(s):
    import base64
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)).decode(
        "utf-8", "replace")


def extract_checkpoint_links(at, msg_id):
    """Fetch full message, return Meta checkpoint/recovery action links."""
    try:
        d = gmail_get(at, f"messages/{msg_id}?format=full")
    except Exception:
        return []
    bodies = []

    def walk(p):
        data = p.get("body", {}).get("data")
        if data and p.get("mimeType", "").startswith("text/"):
            bodies.append(_b64url(data))
        for c in p.get("parts", []) or []:
            walk(c)
    walk(d.get("payload", {}))
    text = "\n".join(bodies).replace("&amp;", "&")
    links = []
    for m in re.finditer(r'href="([^"]+)"', text):
        u = m.group(1)
        if CHECKPOINT_LINK_RX.fullmatch(u) or CHECKPOINT_LINK_RX.search(u):
            if u not in links:
                links.append(u)
    # also catch bare URLs in plain-text parts
    for u in re.findall(r"https://[^\s\"'<>]+", text):
        u = u.rstrip(".,);]")
        if (CHECKPOINT_LINK_RX.search(u) and "help." not in u
                and u not in links):
            links.append(u)
    return links[:5]


def drive_link(url):
    """Open a recovery/checkpoint link in debug Chrome via CDP /json/new."""
    try:
        req = urllib.request.Request(
            f"{CDP_URL}/json/new?{urllib.parse.quote(url, safe='')}",
            method="PUT")
        urllib.request.urlopen(req, timeout=10).read()
        return True
    except Exception:
        try:
            req = urllib.request.Request(
                f"{CDP_URL}/json/new?{urllib.parse.quote(url, safe='')}")
            urllib.request.urlopen(req, timeout=10).read()
            return True
        except Exception:
            return False


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
    driven = set(st.get("driven", []))
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
        # Auto-drive fresh Meta checkpoint/recovery action links — these
        # expire fast and nothing else opens them (suspension recovery,
        # hacked-account disavow, account-disabled flows).
        if not dry and not seed and n["cls"] in ("security", "recovery"):
            try:
                for u in extract_checkpoint_links(at, n["id"]):
                    if u in driven:
                        continue
                    ok = drive_link(u)
                    driven.add(u)
                    st.setdefault("driven", []).append(u)
                    st["driven"] = st["driven"][-200:]
                    print(f"[drive] {'opened' if ok else 'FAILED'} {u[:100]}")
                    if not dry:
                        alert("ops", "Meta mail watchdog auto-opened "
                              f"recovery link ({n['cls']}): {u[:140]}")
            except Exception as e:
                print(f"[drive] error {e}")
    if not dry:
        save_state(st)  # persist driven-link dedupe too
    print(f"scanned={len(msgs)} new={len(new)} "
          f"classes={counts} state_saved={not dry}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
