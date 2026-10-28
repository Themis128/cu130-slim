#!/usr/bin/env python3
"""Compare SocialAuto .env + live sidecar/DNS against official TikTok
expectations."""

import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (EXPECTED_REDIRECT, EXPECTED_SCOPES, SIDECAR,  # noqa: E402
                     cf_zone_id, env_key, tiktok_txt_records)

ok = fail = 0


def check(name: str, cond: bool, detail: str) -> None:
    global ok, fail
    if cond:
        print(f"OK   {name} — {detail}")
        ok += 1
    else:
        print(f"FAIL {name} — {detail}")
        fail += 1


print("=== TikTok config vs official docs ===")
ck, cs = env_key("TIKTOK_CLIENT_KEY"), env_key("TIKTOK_CLIENT_SECRET")
ru, de = env_key("TIKTOK_REDIRECT_URI"), env_key("TIKTOK_DEV_EMAIL")
dp = env_key("TIKTOK_DEV_PASSWORD")

print(f"client_key:          {'set (' + str(len(ck)) + ' chars)' if ck else 'MISSING'}")
print(f"client_secret:       {'set' if cs else 'MISSING'}")
print(f"redirect_uri:        {ru or 'MISSING'}")
print(f"dev_email:           {de or 'MISSING'}")
print(f"dev_password:        {'set' if dp else 'MISSING'}")

check("client_key", bool(ck), "present" if ck else "missing")
check("client_secret", bool(cs), "present" if cs else "missing")
check("redirect_uri", ru == EXPECTED_REDIRECT,
      ru if ru == EXPECTED_REDIRECT else f"want {EXPECTED_REDIRECT} got {ru or 'empty'}")
check("dev_email", bool(de), "present" if de else "missing")
check("dev_password", bool(dp), "present" if dp else "missing")

print(f"\nExpected scopes (OAuth): {EXPECTED_SCOPES}")
print("Expected domain verify:  cloudless.gr (tiktok-domain-verification TXT)")
print("Publish pre-audit:       MEDIA_UPLOAD (not DIRECT_POST)\n")

try:
    with urllib.request.urlopen(f"{SIDECAR}/health", timeout=10) as r:
        d = json.loads(r.read().decode())
        print(f"sidecar: {d}")
        check("sidecar_session", bool(d.get("has_session")),
              f"has_session={d.get('has_session')}")
except Exception:
    check("sidecar", False, "unreachable")

zone_id = cf_zone_id()
if zone_id:
    kinds = [r["kind"] for r in tiktok_txt_records(zone_id)]
    print(f"DNS tiktok TXT kinds: {kinds or ['none']}")
    print(f"domain_verified_dns: {'domain' in kinds}")
    print(f"site_verification_only: {kinds == ['site']}")
else:
    print("DNS: no Cloudflare token or zone lookup failed")

try:
    urllib.request.urlopen("http://127.0.0.1:8083/health", timeout=10)
    check("social_api", True, "healthy")
except Exception:
    check("social_api", False, "down")

print(f"\nSummary: {ok} ok, {fail} fail")
sys.exit(0 if fail == 0 else 1)
