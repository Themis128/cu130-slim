"""Fetch the newest verification/OTP code from the cloudless.gr mail server.

Usage: fetch-code.py [sender-substring] [subject-substring] [max-age-mins]
Defaults target TikTok mails on tbaltzakis@cloudless.gr (dovecot on omv-ha).
Prints the newest 4-8 digit code found, newest first.
"""
import re
import shlex
import subprocess
import sys

sender = sys.argv[1] if len(sys.argv) > 1 else "tiktok"
subject = sys.argv[2] if len(sys.argv) > 2 else "tiktok"
age = sys.argv[3] if len(sys.argv) > 3 else "30m"
user = "tbaltzakis@cloudless.gr"

cmd = [
    "ssh", "omv-ha",
    f"sudo -n doveadm fetch -u {user} 'uid body' mailbox '*' "
    f"FROM {shlex.quote(sender)} SUBJECT {shlex.quote(subject)} SENTSINCE {shlex.quote(age)} 2>/dev/null"
]
out = subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout
# split per-message on 'uid:' headers, newest uid last → scan in reverse
blocks = re.split(r'(?=uid: \d+)', out)
for b in reversed(blocks):
    m = re.search(r'(?:code|is)[^0-9]{0,40}\b(\d{4,8})\b', b, re.I)
    if m:
        print(m.group(1))
        sys.exit(0)
    m = re.search(r'>\s*(\d{6})\s*<', b)
    if m:
        print(m.group(1))
        sys.exit(0)
sys.exit("no code found")
