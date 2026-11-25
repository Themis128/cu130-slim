#!/usr/bin/env python3
"""li_export_watch.py — poll the mailbox for LinkedIn's export-ready email,
download the ZIP, extract Connections.csv into Downloads so audience_watch
picks it up. Run after requesting the export once in LinkedIn's UI.

    li_export_watch.py             # poll once + exit when found
    li_export_watch.py --daemon    # poll every 10 min
"""
import os
import re
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

# Mailbox lane caveat (2026-11-25): the export-ready email goes to the
# Gmail the LinkedIn account is registered on — omv-ha's doveadm will
# usually see nothing. li_export_download.py is the working lane: it
# pulls the archive through the sidecar session's
# settingsApiArchivedMemberDataDownload endpoint. This watcher still
# covers setups where the notification lands on the omv-ha mailbox.
USER = os.environ.get("OMV_SSH_USER", "tbaltzakis")
HOST = os.environ.get("OMV_SSH_HOST", "omv-ha")
KEY = os.path.expanduser(os.environ.get("OMV_SSH_KEY", "~/.ssh/omv_id_rsa"))
OUT = Path("/mnt/c/Users/tbaltzakis/Downloads")
LINK = re.compile(r"https://[^\s\"'>]+linkedin\.com[^\s\"'>]*", re.I)

def newest_export_link():
    cmd = ["ssh", "-i", KEY, f"{USER}@{HOST}",
           "sudo -n doveadm fetch -u tbaltzakis@cloudless.gr 'uid body' "
           "mailbox INBOX SINCE 2d FROM linkedin 2>/dev/null | tail -200"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    for m in LINK.findall(r.stdout):
        if "export" in m.lower() or "download" in m.lower():
            return m
    return None

def fetch_and_extract(url: str) -> Path | None:
    tmp = Path("/tmp/li_export.zip")
    urllib.request.urlretrieve(url, tmp)
    with zipfile.ZipFile(tmp) as z:
        for n in z.namelist():
            if "connection" in n.lower() and n.endswith(".csv"):
                out = OUT / "Connections.csv"
                out.write_bytes(z.read(n))
                return out
    return None

def main() -> int:
    daemon = "--daemon" in sys.argv
    while True:
        link = newest_export_link()
        if link:
            out = fetch_and_extract(link)
            print("extracted:", out or "no Connections.csv in zip")
            return 0
        print("no export email yet")
        if not daemon:
            return 1
        time.sleep(600)

if __name__ == "__main__":
    sys.exit(main())
