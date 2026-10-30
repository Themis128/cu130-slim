#!/usr/bin/env python3
"""omv_disk — trigger + verify the omv-disk-prune workflow (sda1 k3s SSD).

    omv_disk.py status    # latest df numbers from the OMV alert mails
    omv_disk.py run       # gh workflow run omv-disk-prune.yml
    omv_disk.py watch     # poll the latest run until done, print df delta
"""
import json
import os
import subprocess
import sys
import time
import imaplib

REPO = "Themis128/cloudless.gr"


def _gh(*args):
    return subprocess.run(["gh"] + list(args), capture_output=True,
                          text=True, timeout=60)


def status():
    """Latest disk-usage line from the hourly OMV alert mail."""
    m = imaplib.IMAP4_SSL(os.getenv("IMAP_HOST", "192.168.1.130"), 993)
    m.login("tbaltzakis@cloudless.gr", os.environ["MAILBOX_PASSWORD"])
    m.select("INBOX")
    _, d = m.search(None, '(SUBJECT "Disk" SINCE "29-Oct-2026")')
    nums = d[0].split() or []
    if not nums:
        print("no disk alerts"); return
    _, msg = m.fetch(nums[-1], "(BODY[HEADER.FIELDS (SUBJECT)])")
    print(msg[0][1].decode("utf-8", "replace").split("Subject:")[-1].strip()[:80])
    m.logout()


def run():
    r = _gh("workflow", "run", "omv-disk-prune.yml", "--repo", REPO)
    print(r.stderr.strip() or r.stdout.strip() or "triggered")


def watch():
    for _ in range(30):
        r = _gh("run", "list", "--workflow", "omv-disk-prune.yml",
                "--repo", REPO, "--limit", "1",
                "--json", "status,conclusion,databaseId")
        try:
            run = json.loads(r.stdout)[0]
        except Exception:
            time.sleep(10); continue
        print(run["status"], run.get("conclusion") or "…")
        if run["status"] == "completed":
            _gh("run", "view", str(run["databaseId"]), "--repo", REPO,
                "--log").stdout
            logs = _gh("run", "view", str(run["databaseId"]),
                       "--repo", REPO, "--log").stdout
            for line in logs.splitlines():
                if "%" in line and "/dev/" in line:
                    print(line.strip()[:90])
            return
        time.sleep(20)


if __name__ == "__main__":
    {"status": status, "run": run, "watch": watch}.get(
        sys.argv[1] if len(sys.argv) > 1 else "status", status)()
