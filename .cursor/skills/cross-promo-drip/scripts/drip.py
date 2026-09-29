#!/usr/bin/env python3
"""Cross-promotion drip scheduler for SocialAuto.

Analyzes each planned copy through /api/v1/ai/analyze-content, then
optionally creates scheduled posts and verifies verbatim storage.

Usage:
    python3 drip.py plan.json              # analyze only (dry run)
    python3 drip.py plan.json --schedule   # analyze + create scheduled posts
    python3 drip.py plan.json --verify     # re-check created posts

plan.json:
{
  "campaign": "fb-stars-push",
  "posts": [
    {
      "key": "LI-1",
      "platform": "linkedin",
      "text": "...",
      "account_ids": ["uuid"],
      "scheduled_at": "2026-10-01T13:00:00Z",
      "media_ids": ["uuid"]            // optional
    }
  ]
}

Safety:
- Fail-closed quality gate: a post is created only when the analyzer
  returns a numeric SEO score >= 90 AND a successful spellcheck with
  zero issues. Plain-English flags are tolerated only when every match
  is a URL or #hashtag (documented analyzer false-positive).
- Idempotent: keys already in <plan>.created.json are skipped, and a
  post whose exact text is already scheduled to the same platform is
  not duplicated.
- Verbatim check: if stored content_text differs from the draft, the
  created post is deleted immediately and counted as a failure.
- Last-tier guard: Twitter/X and TikTok are "opportunistic only" per
  the 2-Platform Rule — scheduled plans targeting them are rejected.
- Credentials: refuses cleartext http:// for non-localhost API URLs.

Reads SOCIAL_ADMIN_EMAIL / SOCIAL_ADMIN_PASSWORD from repo .env.
API base: SOCIAL_API_URL or http://127.0.0.1:8083
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
API = os.environ.get("SOCIAL_API_URL", "http://127.0.0.1:8083")
MIN_SEO = 90
# 2-Platform Rule (creator-type-voice skill): X + TikTok are opportunistic
# only — never schedule content into last-tier platforms.
LAST_TIER = {"twitter", "tiktok"}
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
# Analyzer false-positive: URLs and hashtags are flagged as
# "long_uncommon_words". Only these may pass the PE gate.
PE_OK = re.compile(r"^(#\w+|https?://\S+|[\w.-]+\.[a-z]{2,}(/\S*)?)$", re.I)


def check_api_url() -> None:
    parsed = urllib.parse.urlparse(API)
    if parsed.scheme == "http" and parsed.hostname not in LOCAL_HOSTS:
        raise SystemExit(
            f"Refusing cleartext http:// for non-localhost API: {API}. "
            "Use https:// or run against the local stack."
        )


def load_env() -> tuple[str, str]:
    env = {}
    for line in (ROOT / ".env").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env["SOCIAL_ADMIN_EMAIL"], env["SOCIAL_ADMIN_PASSWORD"]


def req(path: str, token: str, body: dict | None = None, method: str = "POST"):
    url = f"{API}{path}"
    headers = {"Authorization": f"Bearer {token}"}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    return json.loads(urllib.request.urlopen(r, timeout=90).read())


def login() -> str:
    email, password = load_env()
    r = urllib.request.Request(
        f"{API}/api/v1/auth/login",
        data=urllib.parse.urlencode({"username": email, "password": password}).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    return json.loads(urllib.request.urlopen(r, timeout=30).read())["access_token"]


def analyze(token: str, post: dict) -> dict:
    return req(
        "/api/v1/ai/analyze-content", token,
        {"content": post["text"], "platform": post["platform"]},
    )


def gate(token: str, post: dict) -> tuple[bool, str]:
    """Fail-closed quality gate. Returns (passed, reason)."""
    rep = analyze(token, post)
    seo_obj = rep.get("seo_score")
    seo = seo_obj.get("overall") if isinstance(seo_obj, dict) else None
    spell = rep.get("spellcheck_issues")
    pe = rep.get("plain_english_issues")

    if not isinstance(seo, (int, float)):
        return False, "analyzer unavailable (seo_score missing) — not scheduling"
    if spell is None:
        return False, "spellcheck unavailable — not scheduling"
    if seo < MIN_SEO:
        recs = (seo_obj.get("recommendations") or [])[:3]
        return False, f"seo={seo} < {MIN_SEO} {recs}"
    if len(spell):
        return False, f"spellcheck issues: {spell[:3]}"
    bad_pe = [
        m for i in (pe or [])
        for m in (i.get("matches") or [i] if isinstance(i, dict) else [i])
        if not PE_OK.match(str(m))
    ]
    if bad_pe:
        return False, f"plain-English issues: {bad_pe[:3]}"
    return True, f"seo={seo} spell=0"


def scheduled_texts(token: str) -> set[tuple[str, str]]:
    """(platform, content_text) pairs already in the scheduled queue."""
    d = req("/api/v1/content/posts?status=scheduled&limit=100", token, method="GET")
    out = set()
    for p in d.get("posts") or d.get("items") or []:
        for t in p.get("post_targets") or p.get("targets") or []:
            out.add((t.get("platform"), p.get("content_text")))
    return out


def main() -> int:
    plan_file = Path(sys.argv[1])
    mode = "analyze"
    if "--schedule" in sys.argv:
        mode = "schedule"
    elif "--verify" in sys.argv:
        mode = "verify"

    check_api_url()
    plan = json.loads(plan_file.read_text())
    posts = plan.get("posts") or []
    if not posts:
        print("plan has no posts")
        return 1

    created_file = plan_file.with_suffix(".created.json")
    prior: dict[str, str] = {}
    if created_file.exists():
        prior = (json.loads(created_file.read_text()).get("post_ids") or {})

    token = login()
    print(f"Campaign: {plan.get('campaign', plan_file.stem)} — {len(posts)} posts [{mode}]")

    created_ids: dict[str, str] = dict(prior)
    failures = 0

    for post in posts:
        key = post["key"]

        if post.get("platform") in LAST_TIER:
            if mode == "schedule":
                print(f"{key}: REJECTED — {post['platform']} is last-tier "
                      "(opportunistic only, no schedules). Publish manually.")
                failures += 1
                continue
            print(f"{key}: warning — last-tier platform (analyze only)")

        if mode == "verify":
            pid = post.get("post_id") or prior.get(key)
            if not pid:
                print(f"{key}: no post_id recorded — FAILED")
                failures += 1
                continue
            d = req(f"/api/v1/content/posts/{pid}", token, method="GET")
            verbatim = d.get("content_text") == post["text"]
            targets = d.get("post_targets") or d.get("targets") or []
            tinfo = [(t.get("platform"), t.get("status")) for t in targets]
            print(f"{key}: status={d.get('status')} verbatim={verbatim} targets={tinfo}")
            if not verbatim or not targets:
                failures += 1
            continue

        passed, reason = gate(token, post)
        print(f"{key}: {reason}")

        if mode == "schedule":
            if not passed:
                failures += 1
                continue
            if key in created_ids:
                print(f"  -> already created as {created_ids[key]} — skipping")
                continue
            if (post["platform"], post["text"]) in scheduled_texts(token):
                print("  -> identical text already scheduled to this platform — skipping")
                created_ids[key] = "(existing)"
                continue
            body = {
                "content_text": post["text"],
                "target_account_ids": post["account_ids"],
                "scheduled_at": post["scheduled_at"],
                "status": "scheduled",
            }
            if post.get("media_ids"):
                body["media_ids"] = post["media_ids"]
            try:
                r = req("/api/v1/content/posts", token, body)
                pid = r.get("id")
                d = req(f"/api/v1/content/posts/{pid}", token, method="GET")
                verbatim = d.get("content_text") == post["text"]
                targets = d.get("post_targets") or d.get("targets") or []
                ok = verbatim and d.get("status") == "scheduled" and targets
                print(f"  -> {pid} status={d.get('status')} verbatim={verbatim} "
                      f"targets={[(t.get('platform'), t.get('status')) for t in targets]}")
                if not ok:
                    req(f"/api/v1/content/posts/{pid}", token, method="DELETE")
                    print(f"  !! verbatim/target check failed — deleted {pid}")
                    failures += 1
                else:
                    created_ids[key] = pid
            except urllib.error.HTTPError as e:
                print(f"  !! create failed: HTTP {e.code} {e.read()[:200]}")
                failures += 1

    if mode == "schedule" and created_ids:
        created_file.write_text(json.dumps(
            {"campaign": plan.get("campaign"), "post_ids": created_ids}, indent=1))
        print(f"Post IDs saved to {created_file}")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
