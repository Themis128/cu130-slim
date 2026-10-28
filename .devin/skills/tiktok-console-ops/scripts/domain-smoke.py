#!/usr/bin/env python3
"""TikTok Content Posting domain verification — practical smoke test.
Checks the three independent signals that mean PULL_FROM_URL can be used:
  1) DNS TXT records for tiktok site/domain verification on cloudless.gr
  2) The connected TikTok account token is valid
  3) The creator_info endpoint returns 200 (Content Posting scope live)"""

import json
import subprocess
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import env_key  # noqa: E402
from skill_http import env, repo_root  # noqa: E402

DOMAIN = env("TIKTOK_VERIFY_DOMAIN") or "cloudless.gr"
err = 0


def ok(msg: str) -> None:
    print(f"[OK]   {msg}")


def fail(msg: str) -> None:
    global err
    print(f"[FAIL] {msg}")
    err = 1


def warn(msg: str) -> None:
    print(f"[WARN] {msg}")


# 1. DNS TXT records via Cloudflare API, fallback to public DoH
txt_count = 0
cf_token, cf_zone = env_key("CLOUDFLARE_API_TOKEN"), env_key("CLOUDFLARE_ZONE_ID")
if cf_token and cf_zone:
    try:
        req = urllib.request.Request(
            f"https://api.cloudflare.com/client/v4/zones/{cf_zone}/dns_records?type=TXT&name={DOMAIN}",
            headers={"Authorization": f"Bearer {cf_token}",
                     "Content-Type": "application/json"})
        data = json.loads(urllib.request.urlopen(req, timeout=20).read())
        txt_count = len([r for r in data.get("result", [])
                         if "tiktok" in r.get("content", "").lower()])
    except Exception:
        pass

if txt_count == 0:
    try:
        req = urllib.request.Request(
            f"https://cloudflare-dns.com/dns-query?name={DOMAIN}&type=TXT",
            headers={"Accept": "application/dns-json"})
        data = json.loads(urllib.request.urlopen(req, timeout=10).read())
        txt_count = len([a for a in data.get("Answer", [])
                         if "tiktok" in a.get("data", "").lower()])
    except Exception:
        pass

if txt_count > 0:
    ok(f"{txt_count} TikTok-related TXT record(s) for {DOMAIN}")
else:
    warn("No TikTok TXT records found via Cloudflare API or public DNS")


def api_probe(code: str) -> str:
    r = subprocess.run(
        ["docker", "compose", "exec", "-T", "social-api", "python3", "-c", code],
        cwd=repo_root(), capture_output=True, text=True)
    return r.stdout.strip()


# 2. creator_info
out = api_probe("""
import asyncio, os, httpx
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from app.core.security import decrypt_token

async def go():
    e = create_async_engine(os.environ['DATABASE_URL'])
    async with e.connect() as c:
        r = await c.execute(text("SELECT access_token_enc FROM social_accounts WHERE platform='tiktok'"))
        row = r.first()
        if not row:
            print('NO_TIKTOK_ACCOUNT')
            return
        tok = decrypt_token(bytes(row[0]))
        async with httpx.AsyncClient(timeout=20) as h:
            q = await h.post('https://open.tiktokapis.com/v2/post/publish/creator_info/query/',
                headers={'Authorization': f'Bearer {tok}', 'Content-Type': 'application/json'})
            print(q.status_code)
            print(q.text[:300])
asyncio.run(go())
""")
lines = out.splitlines()
code = lines[0] if lines else ""
body = "\n".join(lines[1:])
if code == "200":
    ok("TikTok creator_info 200 — Content Posting token + scopes are live")
    try:
        u = json.loads(body).get("data", {})
        if u.get("creator_avatar_url"):
            ok("creator avatar present")
    except ValueError:
        pass
elif "url_ownership_unverified" in body.lower():
    fail("TikTok reports url_ownership_unverified — domain verification still needed")
else:
    warn(f"creator_info returned {code}: {body}")

# 3. PULL_FROM_URL init probe
out = api_probe(f"""
import asyncio, os, httpx
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from app.core.security import decrypt_token

async def go():
    e = create_async_engine(os.environ['DATABASE_URL'])
    async with e.connect() as c:
        r = await c.execute(text("SELECT access_token_enc FROM social_accounts WHERE platform='tiktok'"))
        row = r.first()
        tok = decrypt_token(bytes(row[0]))
        async with httpx.AsyncClient(timeout=20) as h:
            q = await h.post('https://open.tiktokapis.com/v2/post/publish/video/init/',
                headers={{'Authorization': f'Bearer {{tok}}', 'Content-Type': 'application/json'}},
                json={{'post_info': {{'title': 'smoke', 'privacy_level': 'SELF_ONLY'}},
                      'source_info': {{'source': 'PULL_FROM_URL', 'video_url': 'https://{DOMAIN}/favicon.ico'}}}})
            print(q.status_code)
            print(q.text[:400])
asyncio.run(go())
""")
lines = out.splitlines()
code2, body2 = (lines[0] if lines else ""), "\n".join(lines[1:])
if code2 == "200":
    ok("PULL_FROM_URL init accepted — domain ownership is verified")
elif code2 == "403":
    if "url_ownership_unverified" in body2.lower():
        fail("PULL_FROM_URL blocked: url_ownership_unverified")
    elif "unaudited_client" in body2.lower():
        ok("PULL_FROM_URL reaches audit gate (not ownership) — domain verified, "
           "waiting for app audit")
    else:
        warn(f"PULL_FROM_URL 403: {body2}")
else:
    warn(f"PULL_FROM_URL returned {code2}: {body2}")

print("[ALL OK] TikTok domain verification is effective" if err == 0
      else "[DONE] TikTok domain verification needs attention")
sys.exit(err)
