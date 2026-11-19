#!/usr/bin/env python3
"""tg_api_setup.py — obtain TG_API_ID / TG_API_HASH from my.telegram.org.

Implements the documented HTTP contract (mirrors my.telegram.org-api-wrapper):
  POST /auth/send_password  {phone}                        -> {"random_hash": ...}
  POST /auth/login          {phone, random_hash, password} -> "true" + stel_token cookie
  GET  /apps                Cookie: stel_token             -> api_id / api_hash
  POST /apps/create         {app fields + hidden hash}     -> creates app if none

The web-login confirmation code is delivered inside the Telegram app to the
"Telegram" service chat. With --phone-adb the code is read off the phone via
uiautomator (see phone-adb-ops); otherwise the code may be passed via --code
or TG_WEB_CODE.

Never prints or writes the api_hash unless --print/--env-file is given.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request

BASE = "https://my.telegram.org"
ADB = os.environ.get("TG_ADB_EXE") or "/mnt/c/Users/tbaltzakis/Android/Sdk/platform-tools/adb.exe"
SERIAL = os.environ.get("TG_ADB_SERIAL") or "RFCTA0AEKZP"


def _post(path: str, fields: dict[str, str], cookie: str | None = None) -> tuple[str, dict[str, str]]:
    boundary = "----tg" + os.urandom(8).hex()
    parts = []
    for k, v in fields.items():
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n")
    data = ("".join(parts) + f"--{boundary}--\r\n").encode()
    req = urllib.request.Request(BASE + path, data=data, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    req.add_header("User-Agent", "Mozilla/5.0")
    if cookie:
        req.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read().decode("utf-8", "replace"), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.read().decode("utf-8", "replace"), dict(e.headers)


def _get(path: str, cookie: str) -> str:
    req = urllib.request.Request(BASE + path)
    req.add_header("Cookie", cookie)
    req.add_header("User-Agent", "Mozilla/5.0")
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def _stel_cookie(headers: dict[str, str]) -> str | None:
    raw = headers.get("Set-Cookie") or headers.get("set-cookie") or ""
    m = re.search(r"stel_token=[^;]+", raw)
    return m.group(0) if m else None


def _adb(cmd: str) -> str:
    return subprocess.run(
        [ADB, "-s", SERIAL] + cmd.split(), capture_output=True, text=True, timeout=30
    ).stdout


def _ui_xml() -> str:
    _adb("shell uiautomator dump /sdcard/_tgcode.xml")
    return _adb("shell cat /sdcard/_tgcode.xml")


def _latest_web_code() -> str | None:
    """Newest 'log in on my.telegram.org ... login code: XXXX' message in the service chat."""
    xml = _ui_xml()
    codes = re.findall(r"my\.telegram\.org[\s\S]{0,400}?login code:\s*(?:&#10;|\n)?\s*([A-Za-z0-9]{6,20})", xml)
    return codes[-1] if codes else None


def _extract_creds(html: str) -> tuple[str | None, str | None]:
    id_m = re.search(r'for="app_id"[\s\S]{0,200}?<strong>(\d+)</strong>', html)
    if not id_m:
        id_m = re.search(r"api_id[^0-9]{0,80}?(\d{4,10})", html)
    hash_m = re.search(r'for="app_hash"[\s\S]{0,200}?<span[^>]*>([a-f0-9]{32})</span>', html)
    if not hash_m:
        hash_m = re.search(r"api_hash[^a-f0-9]{0,80}?([a-f0-9]{32})", html)
    return (id_m.group(1) if id_m else None, hash_m.group(1) if hash_m else None)


def _create_app(cookie: str) -> bool:
    html = _get("/apps", cookie)
    form = re.search(r'<form[^>]*action="(/apps/create[^"]*)"[^>]*>([\s\S]*?)</form>', html)
    if not form:
        form = re.search(r'<form[\s\S]{0,400}>([\s\S]*?app_title[\s\S]*?)</form>', html)
    if not form:
        return False
    action, body = form.group(1), form.group(2)
    fields: dict[str, str] = {}
    for inp in re.finditer(r'<input[^>]*name="([^"]+)"[^>]*>', body):
        tag = inp.group(0)
        name = inp.group(1)
        val = re.search(r'value="([^"]*)"', tag)
        fields[name] = val.group(1) if val else ""
    fields.update({
        "app_title": "SocialAuto Archive",
        "app_shortname": "socialautoarchive",
        "app_platform": fields.get("app_platform") or "desktop",
        "app_url": "https://social.cloudless.gr",
        "app_desc": "Telegram channel archive reader for Cloudless infra alerts",
    })
    # radio/select platform
    plat = re.search(r'name="app_platform"[^>]*value="([^"]+)"[^>]*checked', body)
    if plat:
        fields["app_platform"] = plat.group(1)
    _post(action.split("?")[0], fields, cookie)
    return True


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--phone", default=os.environ.get("TG_PHONE"), help="E.164 phone, e.g. +306977777838")
    p.add_argument("--code", default=os.environ.get("TG_WEB_CODE"), help="web-login code (else read via ADB)")
    p.add_argument("--phone-adb", action="store_true", help="read the code off the phone via ADB")
    p.add_argument("--env-file", help="append TG_API_ID/TG_API_HASH to this file (default: print id only)")
    p.add_argument("--wait", type=int, default=120, help="seconds to poll for the code")
    args = p.parse_args()

    if not args.phone:
        print("need --phone", file=sys.stderr)
        return 1

    body, _ = _post("/auth/send_password", {"phone": args.phone})
    if "TOO_MANY" in body or "try again" in body.lower():
        print("rate-limited by my.telegram.org — wait and retry", file=sys.stderr)
        return 2
    try:
        random_hash = json.loads(body)["random_hash"]
    except (ValueError, KeyError):
        print(f"unexpected send_password response: {body[:200]}", file=sys.stderr)
        return 1
    print("code requested — waiting for Telegram service message")

    code = args.code
    if not code:
        if not args.phone_adb:
            print("no --code; pass --phone-adb or TG_WEB_CODE", file=sys.stderr)
            return 1
        deadline = time.time() + args.wait
        last = _latest_web_code()
        while time.time() < deadline:
            time.sleep(4)
            c = _latest_web_code()
            if c and c != last:
                code = c
                break
        if not code:
            print("no web-login code seen on phone within timeout", file=sys.stderr)
            return 2
    print("got code")

    body, headers = _post("/auth/login", {
        "phone": args.phone, "random_hash": random_hash, "password": code,
    })
    if body != "true":
        print(f"login rejected: {body[:200]}", file=sys.stderr)
        return 1
    cookie = _stel_cookie(headers)
    if not cookie:
        print("login ok but no stel_token cookie", file=sys.stderr)
        return 1
    print("logged in")

    html = _get("/apps", cookie)
    api_id, api_hash = _extract_creds(html)
    if not (api_id and api_hash):
        print("no app found — creating one")
        _create_app(cookie)
        html = _get("/apps", cookie)
        api_id, api_hash = _extract_creds(html)
    if not (api_id and api_hash):
        print("could not obtain api_id/api_hash", file=sys.stderr)
        return 1

    if args.env_file:
        with open(args.env_file, "a") as f:
            f.write(f"\nTG_API_ID={api_id}\nTG_API_HASH={api_hash}\n")
        print(f"TG_API_ID={api_id} (hash written to {args.env_file})")
    else:
        print(f"TG_API_ID={api_id}")
        print("TG_API_HASH=<redacted> — rerun with --env-file to store")
    return 0


if __name__ == "__main__":
    sys.exit(main())
