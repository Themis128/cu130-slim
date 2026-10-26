#!/usr/bin/env bash
# check-inbox.sh — mailbox triage: counts + non-system unread.
# Needs MAILBOX_PASSWORD env. Skips cloudless.gr system senders.
python3 - <<'PY'
import imaplib, os
pwd = os.environ.get("MAILBOX_PASSWORD")
if not pwd:
    print("ERROR: MAILBOX_PASSWORD not set"); raise SystemExit(1)
m = imaplib.IMAP4_SSL(os.environ.get("IMAP_HOST","192.168.1.130"), int(os.environ.get("IMAP_PORT","993")))
m.login(os.environ.get("MAILBOX_USER","tbaltzakis@cloudless.gr"), pwd)
m.select("INBOX")
_, ids = m.search(None, "UNSEEN")
msgs = ids[0].split()
print(f"unread: {len(msgs)}")
skip = ("cloudless.gr","socialauto","safedeploy","noreply","alerts@","cron")
shown = 0
for mid in msgs[-40:]:
    _, h = m.fetch(mid, "(BODY[HEADER.FIELDS (FROM SUBJECT)])")
    raw = h[0][1].decode(errors="replace")
    fr = next((l for l in raw.split("\r\n") if l.startswith("From:")), "")
    su = next((l for l in raw.split("\r\n") if l.startswith("Subject:")), "")
    if any(x in fr.lower() for x in skip): continue
    print(f"  {fr[:70]}\n  {su[:85]}\n"); shown += 1
    if shown >= 15: break
PY
