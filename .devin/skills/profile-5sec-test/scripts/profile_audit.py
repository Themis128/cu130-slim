#!/usr/bin/env python3
"""5-Second Profile Test audit — scores every connected social account
against the Visibility Era checklist (Day 4: profile photo, handle,
identity consistency, bio, link).

Runs entirely through SocialAuto (/api/v1/profile) + DMR vision model
(ai/qwen3-vl) for avatar quality. Read-only — never writes.

Usage:
  SA_TOKEN=<bearer> python3 profile_audit.py [--brand cloudless] [--json]
  # or creds login (2FA aware):
  SA_EMAIL=... SA_PASSWORD=... SA_OTP=... python3 profile_audit.py

Exit code 0 always; findings are in the report.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import urllib.request

API = os.environ.get("SOCIAL_API_URL", "http://localhost:8083/api/v1")
DMR = os.environ.get("DMR_URL", "http://localhost:12435/engines/v1")
VISION_MODEL = os.environ.get("DMR_VISION_MODEL", "ai/qwen3-vl")

PERSONAL_HINTS = ("themistoklis", "baltzakis", "tbaltzakis", "gmail.com")

HANDLE_OK = re.compile(r"^[a-z0-9][a-z0-9._-]{1,28}[a-z0-9]$", re.I)


def http(method: str, url: str, token: str | None = None, payload: dict | None = None,
         timeout: int = 60) -> dict:
    req = urllib.request.Request(url, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = None
    if payload is not None:
        req.add_header("Content-Type", "application/json")
        data = json.dumps(payload).encode()
    try:
        with urllib.request.urlopen(req, data=data, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except Exception as exc:  # noqa: BLE001
        body = ""
        if hasattr(exc, "read"):
            try:
                body = exc.read().decode()[:300]
            except Exception:
                pass
        return {"_error": f"{exc} {body}".strip()}


def login() -> str:
    if os.environ.get("SA_TOKEN"):
        return os.environ["SA_TOKEN"]
    email, pw = os.environ.get("SA_EMAIL"), os.environ.get("SA_PASSWORD")
    if not (email and pw):
        sys.exit("Set SA_TOKEN or SA_EMAIL+SA_PASSWORD(+SA_OTP)")
    import urllib.parse

    body = {"username": email, "password": pw}
    if os.environ.get("SA_OTP"):
        body["otp"] = os.environ["SA_OTP"]
    req = urllib.request.Request(
        f"{API}/auth/login",
        data=urllib.parse.urlencode(body).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())["access_token"]


def fetch_b64(url: str) -> str | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            blob = r.read()
        if len(blob) > 8_000_000 or len(blob) < 500:
            return None
        return base64.b64encode(blob).decode()
    except Exception:
        return None


def vision_score(avatar_url: str, is_personal: bool) -> dict:
    b64 = fetch_b64(avatar_url)
    if not b64:
        return {"verdict": "unfetched", "notes": "avatar URL not downloadable"}
    kind = ("a person's face (should fill the frame, be well-lit, no "
            "sunglasses/heavy filters)") if is_personal else (
        "a brand logo or mark (should be crisp, high-contrast, readable "
        "as a 40px circle icon, not blurry or cluttered)")
    prompt = (
        f"This is a social media profile picture expected to show {kind}. "
        "Does it pass the 5-second recognition test (a visitor instantly "
        "knows who/what this is at small icon size)? Answer JSON only: "
        '{"pass": true|false, "issues": ["..."], "suggestion": "..."}'
    )
    resp = http(
        "POST", f"{DMR}/chat/completions", payload={
            "model": VISION_MODEL,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ],
            }],
            "max_tokens": 200,
            "temperature": 0,
        }, timeout=120)
    try:
        text = resp["choices"][0]["message"]["content"]
        m = re.search(r"\{.*\}", text, re.S)
        return json.loads(m.group(0)) if m else {"verdict": "parse_fail", "raw": text[:200]}
    except Exception as exc:  # noqa: BLE001
        return {"verdict": "dmr_error", "notes": str(exc)[:200]}


def audit_account(acc: dict, brand: str) -> dict:
    pid = acc["id"]
    prof = http("GET", f"{API}/profile/{pid}", token=acc["_token"])
    if "_error" in prof or "detail" in prof:
        return {"platform": acc["platform"], "username": acc.get("username"),
                "error": (prof.get("_error") or str(prof.get("detail")))[:200]}

    name = prof.get("full_name") or ""
    handle = prof.get("username") or acc.get("username") or ""
    bio = prof.get("biography") or prof.get("about") or ""
    site = prof.get("website") or ""
    avatar = prof.get("profile_pic_url") or prof.get("avatar_url") or ""
    haystack = f"{handle} {name}".lower()
    is_personal = any(h in haystack for h in PERSONAL_HINTS)

    checks = {
        "name_present": bool(name.strip()),
        "name_consistent": is_personal or brand.lower() in name.lower(),
        "handle_typeable": bool(HANDLE_OK.match(handle)),
        "handle_brand": is_personal or brand.lower() in handle.lower(),
        "bio_present": bool(bio.strip()),
        "bio_links_site": "cloudless.gr" in bio or "cloudless.gr" in site,
        "website_set": bool(site.strip()),
        "avatar_present": bool(avatar),
    }
    vision = vision_score(avatar, is_personal) if avatar else {"verdict": "no_avatar"}
    score = sum(1 for v in checks.values() if v)
    return {
        "platform": acc["platform"], "username": handle, "name": name,
        "personal": is_personal, "checks": checks, "score": f"{score}/8",
        "vision": vision,
        "bio": bio[:120], "website": site,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", default="cloudless")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    token = login()
    accounts = http("GET", f"{API}/accounts", token=token)
    items = accounts if isinstance(accounts, list) else accounts.get("items", [])
    for a in items:
        a["_token"] = token

    results = [audit_account(a, args.brand) for a in items]
    if args.json:
        print(json.dumps(results, indent=1))
        return 0

    print(f"\n5-SECOND PROFILE TEST — {len(results)} accounts\n")
    for r in results:
        if "error" in r:
            print(f"  {r['platform']:<10} @{r['username']:<22} ERROR: {r['error']}")
            continue
        fails = [k for k, v in r["checks"].items() if not v]
        vis = r["vision"]
        vtxt = vis.get("pass")
        vtxt = {True: "PASS", False: "FAIL"}.get(vtxt, vis.get("verdict", "?"))
        print(f"  {r['platform']:<10} @{r['username']:<22} score {r['score']}  avatar:{vtxt}")
        if fails:
            print(f"    failing: {', '.join(fails)}")
        if isinstance(vis, dict) and vis.get("suggestion"):
            print(f"    vision: {vis['suggestion'][:120]}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
