#!/usr/bin/env python3
"""cf_maildir_scan.py — click the CF destination-confirm mail via SSH, no IMAP password.

The mailbox password lives only on omv-ha (/etc/dovecot/users) — by design it
never leaves the host. But the Maildir is plain files: SSH to omv-ha, grep the
newest mails for the Cloudflare confirm link, follow it locally. Zero new creds.

SSH tried in order: ssh tbaltzakis@omv-ha, ssh tbaltzakis@100.95.117.84
(Tailscale), then omv (192.168.1.x LAN alias). Falls back to
cf_mail_confirm_watch.py (IMAP) when SSH is unavailable.

Usage: python backend/scripts/cf_maildir_scan.py [--for tbaltzakis@cloudless.gr] [--tries 20]
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

MAILDIR = "/var/mail/vhosts/cloudless.gr/{user}/"
HOSTS = ["omv-ha", "100.95.117.84", "192.168.1.130"]
LINK_RE = re.compile(r"https://[^\s\"'<>]+(?:confirm|verify)[^\s\"'<>]+")


def _ssh(user_host: str, remote_cmd: str, timeout: int = 15) -> str | None:
    try:
        r = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
             "-o", "StrictHostKeyChecking=accept-new", user_host, remote_cmd],
            capture_output=True, text=True, timeout=timeout,
        )
        return r.stdout if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _scan_once(hosts: list[str], target: str) -> str | None:
    local = target.split("@")[0]
    maildir = MAILDIR.format(user=local)
    # Maildir is vmail-owned: the shell glob must expand as root too, so the
    # whole loop runs inside `sudo -n sh -c`. The remote command is base64'd
    # to survive the local-shell + ssh + remote-sh quoting layers.
    import base64
    inner = (
        f'for f in $(ls -t {maildir}new/* {maildir}cur/* 2>/dev/null | head -40); '
        'do grep -il cloudflare "$f" >/dev/null 2>&1 && '
        'grep -oE "https://[^\\" <>]+(confirm|verify)[^\\" <>]+" "$f" | head -1 && break; done'
    )
    cmd = f"echo {base64.b64encode(inner.encode()).decode()} | base64 -d | sudo -n sh"
    for h in hosts:
        out = _ssh(f"tbaltzakis@{h}", cmd)
        if out:
            m = LINK_RE.search(out)
            if m:
                print(f"[ok ] confirm link found via ssh {h}")
                return m.group(0)
    return None


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--for", dest="target", default="tbaltzakis@cloudless.gr")
    p.add_argument("--tries", type=int, default=20)
    p.add_argument("--wait", type=int, default=15)
    args = p.parse_args()

    link = _scan_once(HOSTS, args.target)
    if not link:
        print(f"[ok ] watching omv-ha maildir for the CF confirm mail ({args.target})")
        for _ in range(args.tries):
            link = _scan_once(HOSTS, args.target)
            if link:
                break
            time.sleep(args.wait)
    if not link:
        print("[FIX] no confirm mail found via ssh — fall back to cf_mail_confirm_watch.py (IMAP)")
        watch = Path(__file__).with_name("cf_mail_confirm_watch.py")
        if watch.exists():
            import runpy
            sys.argv = [str(watch), "--for", args.target]
            runpy.run_path(str(watch), run_name="__main__")
        return 1
    try:
        urllib.request.urlopen(urllib.request.Request(link), timeout=20)
        print("[ok ] followed confirm link — destination verified")
        return 0
    except Exception as exc:
        print(f"[FIX] follow failed: {exc}\n      -> open manually: {link}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
