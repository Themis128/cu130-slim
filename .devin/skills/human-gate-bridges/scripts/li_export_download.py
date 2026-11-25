#!/usr/bin/env python3
"""li_export_download.py — fetch the ready LinkedIn data archive through
the live linkedin-browser-sidecar session and drop Connections.csv into
Downloads for audience_watch. Replaces the mailbox lane: the export-ready
email goes to Gmail (unreachable from omv-ha doveadm), but the archive
itself is always downloadable via the session-side endpoint once ready.

Run inside social-api (same docker network as the sidecar), or anywhere
SIDECAR_URL resolves:

    docker exec social-api python3 /app/.devin/.../li_export_download.py
    SIDECAR_URL=http://linkedin-browser-sidecar:9225 python3 li_export_download.py

Flow: /debug/navigate → download-my-data page → the "Download archive"
button fires a credentialed fetch to
/mysettings-api/settingsApiArchivedMemberDataDownload/ which returns the
ZIP inline (CSRF: JSESSIONID cookie value as csrf-token header). We replay
that fetch in-page, base64 the bytes, chunk them out, unzip, extract
Connections.csv → OUT dir.
"""
import base64
import json
import os
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

SIDECAR = os.environ.get("SIDECAR_URL", "http://linkedin-browser-sidecar:9225")
# Runs inside social-api, which has no /mnt/c — default staging is /tmp;
# `docker cp` Connections.csv back to the Windows Downloads dir after.
OUT = Path(os.environ.get("LI_EXPORT_OUT", "/tmp"))
CSRF_JS = "(document.cookie.match(/JSESSIONID=\\\"?([^\\\";]+)/)||[])[1]||''"
DOWNLOAD_EXPR = (
    "return fetch('/mysettings-api/settingsApiArchivedMemberDataDownload/',"
    "{credentials:'include',headers:{'csrf-token':" + CSRF_JS + ","
    "'x-restli-protocol-version':'2.0.0'}})"
    ".then(r=>r.arrayBuffer()).then(b=>{const u=new Uint8Array(b);let s='';"
    "const c=32768;for(let i=0;i<u.length;i+=c){s+=String.fromCharCode.apply("
    "null,u.subarray(i,Math.min(i+c,u.length)))}window.__zip=btoa(s);"
    "return 'bytes:'+u.length+' status:'+''})"
)


def _post(path: str, body: dict, timeout: int = 60) -> dict:
    req = urllib.request.Request(
        SIDECAR + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as f:
        return json.loads(f.read())


def ev(expr: str, timeout: int = 60) -> object:
    return _post("/debug/eval", {"expr": expr}, timeout).get("value")


def archive_ready() -> bool:
    _post("/debug/navigate", {
        "url": "https://www.linkedin.com/mypreferences/d/download-my-data",
        "waitUntil": "domcontentloaded"}, 60)
    time.sleep(5)
    txt = ev(
        "return (document.body.innerText||'').slice(0,3000)", 40) or ""
    return "Download archive" in txt


def fetch_zip(dest: Path) -> int:
    head = ev(DOWNLOAD_EXPR, 90)
    if not (head and str(head).startswith("bytes:")):
        raise RuntimeError(f"archive fetch failed: {head}")
    total = ev("return window.__zip.length", 30)
    buf = bytearray()
    i = 0
    while i < total:
        buf += base64.b64decode(ev(f"return window.__zip.slice({i},{i+600000})", 60))
        i += 600000
    dest.write_bytes(buf)
    return len(buf)


def main() -> int:
    if not archive_ready():
        print("no ready archive — request one at linkedin.com/mypreferences/d/download-my-data")
        return 1
    tmp = Path("/tmp/li_export.zip")
    n = fetch_zip(tmp)
    wrote = []
    with zipfile.ZipFile(tmp) as z:
        for name in z.namelist():
            if "connection" in name.lower() and name.endswith(".csv"):
                out = OUT / "Connections.csv"
                out.write_bytes(z.read(name))
                wrote.append(str(out))
    print(f"downloaded {n} bytes; extracted: {wrote or 'no Connections.csv'}")
    return 0 if wrote else 2


if __name__ == "__main__":
    sys.exit(main())
