#!/usr/bin/env python3
"""mail_triage — sweep tbaltzakis@cloudless.gr for phishing/spam and real issues.

    mail_triage.py scan            # classify + report, no deletes
    mail_triage.py purge           # classify → delete suspects → report
    mail_triage.py subjects [N]    # last N subjects (decoded, crash-safe)

Findings map:
  - phishing/scam sender or subject → "suspect" (purged on `purge`)
  - OMV disk/watchdog/cron noise   → "ops"
  - publish/report/connected mails → "signal"
  - everything else                → "review"
"""
import imaplib
import re
import os
import sys
import email

IMAP_HOST = os.getenv("IMAP_HOST", "192.168.1.130")
USER = "tbaltzakis@cloudless.gr"

SUSPECT = re.compile(
    r"speed\.at|ενημέρωση ασφαλείας|υποχρεωτική|απαιτείται ενέργεια|"
    r"you have won|lottery|inheritance|bitcoin|wallet.*(verif|unlock)|"
    r"seed phrase|viagra|casino|crypto giveaway", re.I)
OPS = re.compile(r"\[OMV\]|nas-auto-remediate|HEARTBEAT|Cron <root@omv>", re.I)
SIGNAL = re.compile(
    r"published to|account is connected|ad report|Strategy brief|Ops health|"
    r"Maintenance Report|Free-promo|recovered", re.I)


def dec(s: str) -> str:
    try:
        return str(email.header.make_header(email.header.decode_header(s)))
    except Exception:
        return s[:70]


def sweep(delete: bool):
    m = imaplib.IMAP4_SSL(IMAP_HOST, 993)
    m.login(USER, os.environ["MAILBOX_PASSWORD"])
    m.select("INBOX")
    _, d = m.search(None, 'ALL')
    groups = {"suspect": [], "ops": [], "signal": [], "review": []}
    for n in (d[0].split() or [])[-300:]:
        _, msg = m.fetch(n, "(BODY[HEADER.FIELDS (SUBJECT FROM)])")
        t = msg[0][1].decode("utf-8", "replace") if msg and msg[0] else ""
        s = re.search(r"Subject: (.*)", t)
        f = re.search(r"From: (.*)", t)
        subj = dec(s.group(1).strip()) if s else ""
        frm = dec(f.group(1).strip()) if f else ""
        hay = f"{subj} {frm}"
        bucket = ("suspect" if SUSPECT.search(hay)
                  else "ops" if OPS.search(hay)
                  else "signal" if SIGNAL.search(hay) else "review")
        groups[bucket].append((n, subj[:60], frm[:35]))
        if delete and bucket == "suspect":
            m.store(n, "+FLAGS", "\\Deleted")
    if delete:
        m.expunge()
    m.logout()
    for b in ("suspect", "review", "signal", "ops"):
        items = groups[b]
        print(f"\n== {b} ({len(items)})")
        for _, s, f in items[:15]:
            print(f"  {s} | {f}")
    return groups


def subjects(n_last: int):
    m = imaplib.IMAP4_SSL(IMAP_HOST, 993)
    m.login(USER, os.environ["MAILBOX_PASSWORD"])
    m.select("INBOX")
    _, d = m.search(None, 'ALL')
    for n in (d[0].split() or [])[-n_last:]:
        _, msg = m.fetch(n, "(BODY[HEADER.FIELDS (SUBJECT DATE)])")
        t = msg[0][1].decode("utf-8", "replace") if msg and msg[0] else ""
        s = re.search(r"Subject: (.*)", t)
        dt = re.search(r"Date: (.*)", t)
        print((dt.group(1).strip() if dt else "?")[:20], "|",
              dec(s.group(1).strip())[:70] if s else "?")
    m.logout()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "scan"
    if cmd == "subjects":
        subjects(int(sys.argv[2]) if len(sys.argv) > 2 else 15)
    else:
        sweep(delete=(cmd == "purge"))
