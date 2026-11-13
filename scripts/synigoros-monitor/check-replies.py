#!/usr/bin/env python3
"""check-replies.py — did anyone from a given domain write to the
cloudless.gr mailbox since a date? Used by admin-followups.js to skip
scheduled escalation emails when the authority already responded.

usage: check-replies.py --from <domain-or-addr> --since YYYY-MM-DD
prints JSON: {"replied": bool, "matches": [{"from":..., "subject":..., "date":...}]}
exit 0 always (fail-open: caller decides); "error" field on failure.
"""
import argparse, imaplib, json, os, re, sys
from email.header import decode_header
from email.utils import parsedate_to_datetime

DIR = os.path.dirname(os.path.abspath(__file__))
ENV = os.path.normpath(os.path.join(DIR, "..", "..", ".env"))


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="sender", required=True)
    ap.add_argument("--since", required=True)  # YYYY-MM-DD
    a = ap.parse_args()

    host = env_val("IMAP_HOST") or "192.168.1.130"
    port = int(env_val("IMAP_PORT") or 993)
    user = env_val("IMAP_USER") or env_val("SMTP_USER") or "tbaltzakis@cloudless.gr"
    pw = env_val("IMAP_PASSWORD") or env_val("SMTP_PASSWORD")
    if not pw:
        print(json.dumps({"replied": False, "error": "no mailbox password in .env"}))
        return

    # IMAP SINCE wants 'dd-Mon-yyyy'
    mon = {1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun",
           7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec"}
    y, m, d = (int(x) for x in a.since.split("-"))
    since = "%02d-%s-%04d" % (d, mon[m], y)

    out = {"replied": False, "matches": []}
    try:
        mail = imaplib.IMAP4_SSL(host, port)
        mail.login(user, pw)
        mail.select("INBOX")
        _, data = mail.search(None, "FROM", '"%s"' % a.sender, "SINCE", since)
        ids = data[0].split() if data and data[0] else []
        for mid in ids[-10:]:
            _, msg = mail.fetch(mid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
            if not msg or not msg[0]:
                continue
            raw = msg[0][1].decode("utf-8", "replace")
            frm = re.search(r"^From:\s*(.+)$", raw, re.M)
            sub = re.search(r"^Subject:\s*(.+)$", raw, re.M)
            out["matches"].append({
                "from": dec(frm.group(1).strip()) if frm else "",
                "subject": dec(sub.group(1).strip()) if sub else "",
            })
        out["replied"] = bool(out["matches"])
        mail.logout()
    except Exception as e:
        out["error"] = str(e)[:200]
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
