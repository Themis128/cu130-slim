#!/usr/bin/env python3
"""nextcloud-dav.py — WebDAV + OCS Share CLI for the omv Nextcloud workspace.

Auth: Nextcloud *app password* (never the account login password).
Reads credentials from, in order:
  1. --dav-url / --user / --password flags
  2. NEXTCLOUD_DAV_URL / NEXTCLOUD_USERNAME / NEXTCLOUD_APP_PASSWORD env vars
  3. The cu130-slim .env file (repo root) if present

NEXTCLOUD_DAV_URL is the per-user files root, e.g.
  https://cloud.cloudless.gr/remote.php/dav/files/tbaltzakis@cloudless.gr

Usage:
  nextcloud-dav.py list   <remote-dir>
  nextcloud-dav.py mkdir  <remote-dir>
  nextcloud-dav.py upload <local-file> <remote-path>
  nextcloud-dav.py move   <remote-path> <remote-dest>
  nextcloud-dav.py delete <remote-path>
  nextcloud-dav.py share  <remote-path> [--password PW] [--label L] [--expire YYYY-MM-DD]
  nextcloud-dav.py download <remote-path> <local-file>

All remote paths are relative to the DAV user root (no /files/<user> prefix).
Public share URLs are created via the OCS Share API (files_sharing app) with
explicit permissions=1 (read-only) — see the skill docs: omitting permissions
hits a Nextcloud bug where the applied mask is wrong.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

DAV_PROPS = """<?xml version="1.0"?>
<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">
  <d:prop><d:getcontenttype/><d:getcontentlength/><d:resourcetype/></d:prop>
</d:propfind>"""


def _load_env_file() -> dict[str, str]:
    env: dict[str, str] = {}
    for cand in (
        Path(__file__).resolve().parents[4] / ".env",  # <repo>/.devin/skills/.../scripts/
        Path.home() / "cu130-slim" / ".env",
    ):
        if cand.exists():
            for line in cand.read_text().splitlines():
                m = re.match(r"^([A-Z_]+)=(.*)$", line.strip())
                if m:
                    env[m.group(1)] = m.group(2).strip().strip('"')
            break
    return env


def _creds(args) -> tuple[str, str, str]:
    env = {**_load_env_file(), **os.environ}
    url = (args.dav_url or env.get("NEXTCLOUD_DAV_URL", "")).rstrip("/")
    user = args.user or env.get("NEXTCLOUD_USERNAME", "")
    pw = args.password or env.get("NEXTCLOUD_APP_PASSWORD", "")
    if not (url and user and pw):
        sys.exit("Missing credentials — set NEXTCLOUD_DAV_URL / NEXTCLOUD_USERNAME / NEXTCLOUD_APP_PASSWORD")
    # OCS base = everything before /remote.php/dav
    return url, user, pw


def _req(url: str, method: str, user: str, pw: str, *,
         data: bytes | None = None, headers: dict | None = None) -> tuple[int, bytes]:
    import base64
    auth = base64.b64encode(f"{user}:{pw}".encode()).decode()
    req = urllib.request.Request(url, method=method, data=data)
    req.add_header("Authorization", f"Basic {auth}")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _q(path: str) -> str:
    return "/".join(urllib.parse.quote(p) for p in path.strip("/").split("/") if p)


def _mkdirs(dav: str, user: str, pw: str, remote_dir: str) -> None:
    path = ""
    for part in remote_dir.strip("/").split("/"):
        path += f"/{part}"
        code, _ = _req(f"{dav}{urllib.parse.quote(path)}", "MKCOL", user, pw)
        if code not in (201, 405):
            sys.exit(f"MKCOL {path}: HTTP {code}")


def cmd_list(args):
    dav, user, pw = _creds(args)
    code, body = _req(f"{dav}/{_q(args.dir)}/", "PROPFIND", user, pw,
                      data=DAV_PROPS.encode(), headers={"Depth": "1"})
    if code != 207:
        sys.exit(f"PROPFIND {args.dir}: HTTP {code}\n{body[:400]}")
    ns = {"d": "DAV:", "oc": "http://owncloud.org/ns"}
    root = ET.fromstring(body)
    for resp in root.findall("d:response", ns):
        href = (resp.findtext("d:href", default="", namespaces=ns) or "")
        rtype = resp.find(".//d:resourcetype/d:collection", ns)
        kind = "dir " if rtype is not None else "file"
        size = resp.findtext(".//d:getcontentlength", default="-", namespaces=ns) or "-"
        name = urllib.parse.unquote(href.rstrip("/")).split("/")[-1] or href
        print(f"{kind} {size:>12}  {name}")


def cmd_mkdir(args):
    dav, user, pw = _creds(args)
    _mkdirs(dav, user, pw, args.dir)
    print(f"mkdir ok: {args.dir}")


def cmd_upload(args):
    dav, user, pw = _creds(args)
    remote = args.remote.strip("/")
    _mkdirs(dav, user, pw, str(Path(remote).parent))
    data = Path(args.local).read_bytes()
    code, body = _req(f"{dav}/{_q(remote)}", "PUT", user, pw, data=data)
    if code not in (200, 201, 204):
        sys.exit(f"PUT {remote}: HTTP {code}\n{body[:400]}")
    print(f"uploaded {len(data)} bytes → {remote}")


def cmd_download(args):
    dav, user, pw = _creds(args)
    code, body = _req(f"{dav}/{_q(args.remote)}", "GET", user, pw)
    if code != 200:
        sys.exit(f"GET {args.remote}: HTTP {code}")
    Path(args.local).write_bytes(body)
    print(f"downloaded {len(body)} bytes → {args.local}")


def cmd_move(args):
    dav, user, pw = _creds(args)
    dest_dir = str(Path(args.dest.strip("/")).parent)
    _mkdirs(dav, user, pw, dest_dir)
    code, body = _req(
        f"{dav}/{_q(args.src)}", "MOVE", user, pw,
        headers={"Destination": f"{dav}/{_q(args.dest)}"},
    )
    if code not in (201, 204):
        sys.exit(f"MOVE {args.src}: HTTP {code}\n{body[:400]}")
    print(f"moved → {args.dest}")


def cmd_delete(args):
    dav, user, pw = _creds(args)
    code, body = _req(f"{dav}/{_q(args.remote)}", "DELETE", user, pw)
    if code not in (200, 204):
        sys.exit(f"DELETE {args.remote}: HTTP {code}\n{body[:400]}")
    print(f"deleted {args.remote}")


def cmd_share(args):
    dav, user, pw = _creds(args)
    ocs = dav.split("/remote.php/dav")[0].rstrip("/") + "/ocs/v2.php/apps/files_sharing/api/v1/shares"
    path = "/" + args.remote.strip("/")
    form = {"path": path, "shareType": "3", "permissions": "1"}
    if args.password:
        form["password"] = args.password
    if args.label:
        form["label"] = args.label
    if args.expire:
        form["expireDate"] = args.expire
    code, body = _req(
        ocs, "POST", user, pw,
        data=urllib.parse.urlencode(form).encode(),
        headers={"OCS-APIRequest": "true", "Content-Type": "application/x-www-form-urlencoded"},
    )
    if code != 200:
        sys.exit(f"share {path}: HTTP {code}\n{body[:400]}")
    m_url = re.search(rb"<url>([^<]+)</url>", body)
    m_tok = re.search(rb"<token>([^<]+)</token>", body)
    if not m_url:
        sys.exit(f"share created but no URL in response:\n{body[:600]}")
    print(m_url.group(1).decode())
    if args.verbose:
        print(f"token: {m_tok.group(1).decode() if m_tok else '?'}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description="Nextcloud WebDAV/OCS CLI")
    ap.add_argument("--dav-url")
    ap.add_argument("--user")
    ap.add_argument("--password")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list"); p.add_argument("dir"); p.set_defaults(fn=cmd_list)
    p = sub.add_parser("mkdir"); p.add_argument("dir"); p.set_defaults(fn=cmd_mkdir)
    p = sub.add_parser("upload"); p.add_argument("local"); p.add_argument("remote"); p.set_defaults(fn=cmd_upload)
    p = sub.add_parser("download"); p.add_argument("remote"); p.add_argument("local"); p.set_defaults(fn=cmd_download)
    p = sub.add_parser("move"); p.add_argument("src"); p.add_argument("dest"); p.set_defaults(fn=cmd_move)
    p = sub.add_parser("delete"); p.add_argument("remote"); p.set_defaults(fn=cmd_delete)
    p = sub.add_parser("share"); p.add_argument("remote")
    p.add_argument("--password"); p.add_argument("--label"); p.add_argument("--expire")
    p.set_defaults(fn=cmd_share)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
