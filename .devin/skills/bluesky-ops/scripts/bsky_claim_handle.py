#!/usr/bin/env python3
"""bsky_claim_handle — claim cloudless.gr on the LIVE SocialAuto Bluesky
account (tbaltzakis.bsky.social) via DNS domain verification.

Flow (fully automated — we control the DNS):
  1. set _atproto.cloudless.gr TXT = did=<our did> via Cloudflare API
  2. com.atproto.identity.updateHandle(handle=cloudless.gr)
  3. PDS validates the TXT → brand handle live on the wired account

    bsky_claim_handle.py check   # TXT + account state
    bsky_claim_handle.py claim   # full flow
"""
import json
import os
import sys
import time
import urllib.request

PDS = "https://bsky.social"
NAME = "_atproto.cloudless.gr"
DID = "did:plc:isl4yqjzxu3s7qwah24cb5xv"
ACC = "fb7dc7b69b662480cd5961a4d1913c78"


def _tok() -> str:
    for p in ("/home/tbaltzakis/cu130-slim/.env",
              "/mnt/c/Users/tbaltzakis/cu130-slim/.env"):
        if os.path.exists(p):
            for l in open(p):
                if l.startswith("CLOUDFLARE_API_TOKEN="):
                    return l.split("=", 1)[1].strip().strip('"')
    sys.exit("no CF token")


def _cf(method, path, body=None):
    r = urllib.request.Request(
        f"https://api.cloudflare.com/client/v4/zones{path}",
        data=json.dumps(body).encode() if body else None, method=method,
        headers={"Authorization": f"Bearer {_tok()}",
                 "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=20).read())


def _zone_id() -> str:
    return _cf("GET", "?name=cloudless.gr")["result"][0]["id"]


def _xrpc(method, payload, tok):
    r = urllib.request.Request(
        f"{PDS}/xrpc/{method}", data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {tok}",
                 "Content-Type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(r, timeout=20).read())


def _session() -> str:
    pw = os.environ.get("BSKY_APP_PASSWORD") or open(
        os.path.expanduser("~/.bsky_app_pw")).read().strip()
    return _xrpc("com.atproto.server.createSession",
                 {"identifier": "tbaltzakis.bsky.social",
                  "password": pw}, "")["accessJwt"]


def check() -> int:
    zid = _zone_id()
    recs = _cf("GET", f"/{zid}/dns_records?name={NAME}&type=TXT")["result"]
    for r in recs:
        print("TXT:", r["content"][:60], "(ours)" if DID in r["content"] else "(other)")
    if not recs:
        print("no _atproto TXT")
    return 0


def claim() -> int:
    zid = _zone_id()
    recs = _cf("GET", f"/{zid}/dns_records?name={NAME}&type=TXT")["result"]
    content = f"did={DID}"
    if recs:
        _cf("PUT", f"/{zid}/dns_records/{recs[0]['id']}",
            {"type": "TXT", "name": NAME, "content": content})
        print("TXT repointed to our DID")
    else:
        _cf("POST", f"/{zid}/dns_records",
            {"type": "TXT", "name": NAME, "content": content})
        print("TXT created")
    time.sleep(20)  # DNS propagation
    tok = _session()
    r = _xrpc("com.atproto.identity.updateHandle",
              {"handle": "cloudless.gr"}, tok)
    print("handle claimed:", r)
    return 0


if __name__ == "__main__":
    sys.exit({"check": check, "claim": claim}[
             sys.argv[1] if len(sys.argv) > 1 else "check"]())
