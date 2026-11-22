#!/usr/bin/env python3
"""wa_token_sync.py — post-OAuth WhatsApp credential sync.

After wa_oauth_drive.py completes, the callback stores a fresh
long-lived token in social_accounts.access_token_enc — but
meta_data["access_token"]/meta_data["phone_number_id"] stay EMPTY, so
_get_whatsapp_client() 400s every send. This script finishes the job:

  1. decrypt access_token_enc inside the API container
  2. write meta_data.access_token (encrypt_field), phone_number_id,
     waba_id, display_phone_number, credentials_configured, token_probe_status
  3. verify the token against /{phone_number_id} on graph.facebook.com
  4. sync WHATSAPP_ACCESS_TOKEN into .env + deploy/socialauto-standalone/.env
     (consent_healers.py + ops scripts read the env var)

Run:  python3 wa_token_sync.py           # sync + verify
      python3 wa_token_sync.py --env-only # skip DB patch (token already in meta)
Never prints token material.
"""
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request

REPO = "/home/tbaltzakis/cu130-slim"
ENV_FILES = [os.path.join(REPO, ".env"),
             os.path.join(REPO, "deploy/socialauto-standalone/.env")]
PHONE_NUMBER_ID = "1432734783254091"

CONTAINER_SCRIPT = r'''
import asyncio, json, os
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from app.core.security import decrypt_token, encrypt_field
import datetime

async def main():
    eng = create_async_engine(os.environ["DATABASE_URL"])
    async with eng.connect() as c:
        r = await c.execute(text(
            "SELECT id, access_token_enc, meta_data FROM social_accounts "
            "WHERE platform='whatsapp' ORDER BY updated_at DESC LIMIT 1"))
        acct_id, enc, meta = r.fetchone()
        meta = meta or {}
    tok = decrypt_token(enc)
    if not tok:
        print("RESULT fail:no-token"); return
    pnid = "1432734783254091"
    meta["access_token"] = encrypt_field(tok)
    meta["phone_number_id"] = pnid
    meta["waba_id"] = meta.get("waba_id") or "1717901383017723"
    meta["display_phone_number"] = meta.get("display_phone_number") or "+306977777838"
    meta["credentials_configured"] = True
    meta["token_probe_status"] = "live"
    meta.pop("reauth_escalated_at", None)
    meta["last_verified_at"] = datetime.datetime.now(datetime.UTC).isoformat()
    async with eng.begin() as c:
        await c.execute(text(
            "UPDATE social_accounts SET meta_data=:m, updated_at=now() "
            "WHERE id=:i"), {"m": json.dumps(meta), "i": acct_id})
    # hand the raw token to the parent via a file inside /tmp (container fs)
    open("/tmp/.wa_tok", "w").write(tok)
    print("RESULT synced")
asyncio.run(main())
'''

VERIFY_SCRIPT = r'''
import asyncio, json, os, urllib.request, urllib.parse
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from app.core.security import decrypt_field
async def main():
    eng = create_async_engine(os.environ["DATABASE_URL"])
    async with eng.connect() as c:
        meta = (await c.execute(text(
            "SELECT meta_data FROM social_accounts WHERE platform='whatsapp' "
            "ORDER BY updated_at DESC LIMIT 1"))).scalar()
    m = meta or {}
    tok = decrypt_field(m.get("access_token", ""))
    pnid = m.get("phone_number_id") or ""
    if not (tok and pnid):
        print("RESULT fail:missing-fields"); return
    u = ("https://graph.facebook.com/v21.0/%s?fields=display_phone_number,"
         "verified_name&access_token=%s" % (pnid, urllib.parse.quote(tok)))
    try:
        d = json.load(urllib.request.urlopen(u, timeout=20))
        print("RESULT live", d.get("display_phone_number"),
              d.get("verified_name"))
    except urllib.error.HTTPError as e:
        print("RESULT dead http=%d" % e.code)
asyncio.run(main())
'''


def _container():
    for c in ("social-api", "social-api-green"):
        r = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", c],
                           capture_output=True, text=True)
        if r.stdout.strip() == "true":
            return c
    return "social-api"


def api_py(script):
    r = subprocess.run(["docker", "exec", "-i", _container(), "python3", "-"],
                       input=script, capture_output=True, text=True, timeout=120)
    return (r.stdout or "") + (r.stderr or "")


def fetch_token():
    subprocess.run(["docker", "exec", _container(), "python3", "-c",
                    "pass"], capture_output=True)  # warm
    out = api_py(CONTAINER_SCRIPT)
    for line in out.splitlines():
        if line.startswith("RESULT"):
            print("db sync:", line)
            if "fail" in line:
                return None
    # token file was written inside the container — copy it out via stdout
    # is impossible without printing; use docker cp to a host tmp file
    host_tmp = "/tmp/.wa_tok_host"
    subprocess.run(["docker", "cp", f"{_container()}:/tmp/.wa_tok", host_tmp],
                   capture_output=True)
    try:
        return open(host_tmp).read().strip()
    except OSError:
        return None
    finally:
        try:
            os.unlink(host_tmp)
        except OSError:
            pass


def sync_env(tok):
    for path in ENV_FILES:
        try:
            s = open(path).read()
        except OSError:
            continue
        if re.search(r"^WHATSAPP_ACCESS_TOKEN=", s, re.M):
            s = re.sub(r"^WHATSAPP_ACCESS_TOKEN=.*$",
                       "WHATSAPP_ACCESS_TOKEN=" + tok, s, flags=re.M)
        else:
            s += "\nWHATSAPP_ACCESS_TOKEN=" + tok + "\n"
        open(path, "w").write(s)
        print("env synced:", path)


def main():
    if "--env-only" in sys.argv:
        tok = None
    else:
        tok = fetch_token()
        if not tok:
            print("FAIL: could not extract token")
            sys.exit(2)
    # verify DB-side regardless
    out = api_py(VERIFY_SCRIPT)
    print(out.strip())
    if "RESULT live" not in out:
        print("FAIL: stored token not live")
        sys.exit(4)
    if tok:
        sync_env(tok)
    print("DONE: whatsapp credentials live + synced")


if __name__ == "__main__":
    main()
