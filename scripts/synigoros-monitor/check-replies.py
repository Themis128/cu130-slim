#!/usr/bin/env python3
"""check-replies.py — did anyone from a given domain write back since a
date? Used by admin-followups.js to skip scheduled escalation emails
when the authority already responded.

Checks BOTH inbound lanes (same as reply-watch.py):
  1. cloudless.gr IMAP (omv-ha dovecot)
  2. Gmail API (baltzakis.themis@gmail.com — the address registered on
     the Ombudsman complaint)

Only human-looking mail counts: noreply@ senders and automated
subjects (OTP, forms, newsletters) are ignored — they are not replies.

usage: check-replies.py --from <domain-or-addr> --since YYYY-MM-DD
       [--require <regex>]   # subject must match (case keywords) to count
prints JSON: {"replied": bool, "matches": [{"lane","from","subject","date"}]}
exit 0 always (fail-open: caller decides); "error" field on failure.
"""
import argparse, importlib.util, imaplib, json, os, re, sys, urllib.parse
from email.header import decode_header

DIR = os.path.dirname(os.path.abspath(__file__))
ENV = os.path.normpath(os.path.join(DIR, "..", "..", ".env"))
GSR = (r"C:\Users\tbaltzakis\AppData\Roaming\devin\skills"
       r"\gmail-send-ops\scripts\gmail-schedule-reply.py")

NOISE = re.compile(r"(κωδικ|οτπ|otp|επαληθευ|ταυτοποίηση|επιβεβαίωση σύνδεσης|"
                   r"password|κωδικός|2fa|dump|bot|has new sign-in|"
                   r"new sign-in|security alert|φόρμα αποστολής|υπενθύμιση|"
                   r"ενημέρωση|επιτυχής)", re.I)
AUTO_SENDER = re.compile(r"(noreply|no-?reply|donotreply|no_reply)", re.I)


def env_val(key):
    try:
        for line in open(ENV, encoding="utf-8", errors="replace"):
            m = re.match(r"^" + re.escape(key) + r'=(.*)$', line.strip())
            if m:
                return m.group(1).strip().strip('"').strip("'")
    except OSError:
        pass
    return None


def dec(s):
    if not s:
        return ""
    return "".join(
        p.decode(c or "utf-8", errors="replace") if isinstance(p, bytes) else p
        for p, c in decode_header(s))


def interesting(frm, subj, require=None):
    if AUTO_SENDER.search(frm or "") or NOISE.search(subj or ""):
        return False
    return not (require and not require.search(subj or ""))


def imap_since(since):
    mon = {1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun",
           7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec"}
    y, m, d = (int(x) for x in since.split("-"))
    return "%02d-%s-%04d" % (d, mon[m], y)


def check_imap(sender, since, require, out):
    user = env_val("IMAP_USER") or env_val("SMTP_USER") or "tbaltzakis@cloudless.gr"
    pw = env_val("IMAP_PASSWORD") or env_val("SMTP_PASSWORD")
    if not pw:
        return
    host = env_val("IMAP_HOST") or "192.168.1.130"
    port = int(env_val("IMAP_PORT") or 993)
    mail = imaplib.IMAP4_SSL(host, port)
    mail.login(user, pw)
    mail.select("INBOX")
    _, data = mail.search(None, "FROM", '"%s"' % sender, "SINCE", imap_since(since))
    ids = data[0].split() if data and data[0] else []
    for mid in ids[-20:]:
        _, msg = mail.fetch(mid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
        raw = msg[0][1].decode("utf-8", "replace") if msg and msg[0] else ""
        frm = re.search(r"^From:\s*(.+)$", raw, re.M)
        sub = re.search(r"^Subject:\s*(.+)$", raw, re.M)
        dat = re.search(r"^Date:\s*(.+)$", raw, re.M)
        f, s = dec(frm.group(1).strip()) if frm else "", dec(sub.group(1).strip()) if sub else ""
        if interesting(f, s, require):
            out["matches"].append({"lane": "cloudless", "from": f[:120],
                                   "subject": s[:160],
                                   "date": dec(dat.group(1).strip())[:60] if dat else ""})
    mail.logout()


def check_gmail(sender, since, require, out):
    spec = importlib.util.spec_from_file_location("gsr", GSR)
    gsr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gsr)
    q = "from:%s after:%s" % (sender, since.replace("-", "/"))
    res = gsr.api("GET", "messages?q=" + urllib.parse.quote(q) + "&maxResults=50")
    for m in res.get("messages", []):
        full = gsr.api("GET", "messages/%s?format=metadata"
                       "&metadataHeaders=From&metadataHeaders=Subject"
                       "&metadataHeaders=Date" % m["id"])
        h = {x["name"].lower(): x["value"]
             for x in full.get("payload", {}).get("headers", [])}
        f, s = h.get("from", ""), h.get("subject", "")
        if interesting(f, s, require):
            out["matches"].append({"lane": "gmail", "from": f[:120],
                                   "subject": s[:160], "date": h.get("date", "")[:60]})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="sender", required=True)
    ap.add_argument("--since", required=True)  # YYYY-MM-DD
    ap.add_argument("--require", default=None)  # regex the subject must match
    a = ap.parse_args()

    require = re.compile(a.require, re.I) if a.require else None
    out = {"replied": False, "matches": []}
    try:
        check_imap(a.sender, a.since, require, out)
    except Exception as e:
        out.setdefault("errors", []).append("imap: " + str(e)[:150])
    try:
        check_gmail(a.sender, a.since, require, out)
    except Exception as e:
        out.setdefault("errors", []).append("gmail: " + str(e)[:150])

    out["replied"] = bool(out["matches"])
    if not out["replied"] and out.get("errors"):
        out["error"] = "; ".join(out["errors"])
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
