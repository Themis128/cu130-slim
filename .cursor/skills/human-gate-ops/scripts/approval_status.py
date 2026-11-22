#!/usr/bin/env python3
"""approval_status — list pending approval cards in Nextcloud Approvals/."""
import base64
import os
import sys
import urllib.request
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))


def _env(key):
    for line in open(os.path.join(HERE, "../../../../.env"), errors="replace"):
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip()
    return ""


def main():
    dav = _env("NEXTCLOUD_DAV_URL").rstrip("/")
    cred = base64.b64encode(
        f"{_env('NEXTCLOUD_USERNAME')}:{_env('NEXTCLOUD_APP_PASSWORD')}".encode()).decode()
    body = b'<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:displayname/></d:prop></d:propfind>'
    r = urllib.request.Request(dav + "/Approvals/", data=body, method="PROPFIND",
        headers={"Authorization": "Basic " + cred, "Depth": "1",
                 "Content-Type": "text/xml"})
    try:
        raw = urllib.request.urlopen(r, timeout=30).read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print("no Approvals/ folder — nothing pending")
            return 0
        raise
    ns = {"d": "DAV:"}
    files = [el.text for el in ET.fromstring(raw).findall(".//d:href", ns)
             if el.text and el.text.endswith(".md")]
    if not files:
        print("no pending approvals")
    for f in files:
        print("•", f.rsplit("/", 1)[-1])
    return 0


if __name__ == "__main__":
    sys.exit(main())
