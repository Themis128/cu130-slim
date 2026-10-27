#!/usr/bin/env python3
"""Pull every "[SocialAuto] Strategy brief" email from the omv-ha mailbox.

The brief is email-only — no DB copy exists — so the mailbox IS the
history. Fetches each message, flattens the HTML part to text, and writes
one file per day to an output dir (default /tmp/briefs/YYYY-MM-DD.txt).

Auth: SMTP_PASSWORD in the repo .env doubles as the dovecot SASL password
(there is no MAILBOX_PASSWORD var). Never print credentials.

Usage:
    python3 fetch_briefs.py [--out DIR] [--subject "Strategy brief"] \
        [--host 192.168.1.130] [--user tbaltzakis@cloudless.gr]
"""
from __future__ import annotations

import argparse
import email
import html
import imaplib
import re
import sys
from email.header import decode_header
from pathlib import Path

_REPO_ENV = Path(__file__).resolve().parents[4] / ".env"


def _load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        m = re.match(r"^([A-Z0-9_]+)=(.*)$", line.strip())
        if m:
            env[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return env


def _dec(s: str | None) -> str:
    if not s:
        return ""
    return "".join(
        p.decode(c or "utf-8", "replace") if isinstance(p, bytes) else p
        for p, c in decode_header(s)
    )


def _html2text(h: str) -> str:
    h = re.sub(r"(?is)<(script|style).*?</\1>", " ", h)
    h = re.sub(r"(?i)<br\s*/?>", "\n", h)
    h = re.sub(r"(?i)</(p|div|tr|li|h[1-6]|table)>", "\n", h)
    h = re.sub(r"(?i)</t[dh]>", " | ", h)
    h = re.sub(r"<[^>]+>", " ", h)
    h = html.unescape(h)
    return re.sub(r"\n\s*\n+", "\n", re.sub(r"[ \t]+", " ", h))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/briefs")
    ap.add_argument("--subject", default="Strategy brief")
    ap.add_argument("--host", default="192.168.1.130")
    ap.add_argument("--port", type=int, default=993)
    ap.add_argument("--user", default="tbaltzakis@cloudless.gr")
    args = ap.parse_args()

    env = _load_env(_REPO_ENV)
    pw = env.get("SMTP_PASSWORD") or env.get("MAILBOX_PASSWORD")
    if not pw:
        print(f"ERROR: SMTP_PASSWORD not found in {_REPO_ENV}", file=sys.stderr)
        return 1

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    mail = imaplib.IMAP4_SSL(args.host, args.port)
    mail.login(args.user, pw)
    mail.select("INBOX")
    _, msgs = mail.search(None, f'(SUBJECT "{args.subject}")')
    ids = msgs[0].split()
    print(f"found {len(ids)} messages matching subject '{args.subject}'")

    for mid in ids:
        _, d = mail.fetch(mid, "(RFC822)")
        msg = email.message_from_bytes(d[0][1])
        subj, date = _dec(msg.get("Subject", "")), msg.get("Date", "")
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    body = part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", "replace"
                    )
                    break
        else:
            body = msg.get_payload(decode=True).decode(
                msg.get_content_charset() or "utf-8", "replace"
            )
        text = _html2text(body) if "<" in body[:500] else body
        m = re.search(r"(\d{4}-\d{2}-\d{2})", subj) or re.search(
            r"(\d{4}-\d{2}-\d{2})", date
        )
        (out / f"{m.group(1) if m else mid.decode()}.txt").write_text(
            f"SUBJECT: {subj}\nDATE: {date}\n\n{text}"
        )
    mail.logout()
    print(f"wrote {len(ids)} briefs to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
