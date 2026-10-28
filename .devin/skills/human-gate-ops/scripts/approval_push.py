#!/usr/bin/env python3
"""approval_push — drops a human-approval card into Nextcloud Approvals/.

    approval_push.py --title "LinkedIn vetting verify" \
        --link "https://..." --context "CM-API review" [--source synigoros]

Creates Approvals/<ts>-<slug>.md on cloud.cloudless.gr via WebDAV — the
user opens the file, clicks the link, acts. Delete the file to close the
approval. Pair with approval_status.py to list what's still pending.
"""
import argparse
import base64
import datetime
import json
import os
import re
import sys
import urllib.request

ENV = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "../../../../.env")


def _env(key):
    for path in (ENV,):
        try:
            for line in open(path, errors="replace"):
                if line.startswith(key + "="):
                    return line.split("=", 1)[1].strip()
        except OSError:
            pass
    return ""


def _creds():
    u, p = _env("NEXTCLOUD_USERNAME"), _env("NEXTCLOUD_APP_PASSWORD")
    if not u or not p:
        raise SystemExit("no NEXTCLOUD creds in .env")
    return base64.b64encode(f"{u}:{p}".encode()).decode()


def _put(url, data):
    r = urllib.request.Request(url, data=data.encode(), method="PUT",
        headers={"Authorization": "Basic " + _creds(),
                 "Content-Type": "text/markdown"})
    return urllib.request.urlopen(r, timeout=30).status


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", required=True)
    ap.add_argument("--link", default="")
    ap.add_argument("--context", default="")
    ap.add_argument("--source", default="agent")
    a = ap.parse_args()
    dav = _env("NEXTCLOUD_DAV_URL").rstrip("/")
    slug = re.sub(r"[^a-z0-9]+", "-", a.title.lower()).strip("-")[:40]
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M")
    body = (f"# {a.title}\n\n- **Context:** {a.context}\n- **Source:** {a.source}"
            f"\n- **Created:** {datetime.datetime.now():%Y-%m-%d %H:%M}\n"
            + (f"\n## ▶ Action: {a.link}\n" if a.link else "")
            + "\n_Close this approval by deleting this file._\n")
    code = _put(f"{dav}/Approvals/{ts}-{slug}.md", body)
    print(json.dumps({"status": code,
                      "file": f"Approvals/{ts}-{slug}.md"}))


if __name__ == "__main__":
    sys.exit(main())
