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
      "media_ids": ["uuid"]            // required for instagram
    }
  ]
}

Safety:
- Fail-closed quality gate: a post is created only when the analyzer
  returns a numeric SEO score >= 90 AND a successful spellcheck with
  zero issues. Plain-English flags are tolerated only when every match
  is a URL or #hashtag (documented analyzer false-positive).
- Idempotent: keys already in <plan>.created.json are skipped when the
  plan entry is unchanged (fingerprint of text/time/accounts/media).
  A changed plan entry for an existing key fails loudly instead of
  silently keeping the stale schedule.
- Duplicate guard: identical text already scheduled to the same
  platform is adopted (its real post ID recorded), never duplicated.
- Verbatim check: if stored content_text differs from the draft, the
  created post is deleted immediately and counted as a failure.
- Instagram posts require media_ids — the platform rejects text-only.
- Last-tier guard: Twitter/X and TikTok are "opportunistic only" per
  the 2-Platform Rule — scheduled plans targeting them are rejected.
- Credentials: refuses cleartext http:// for non-localhost API URLs,
  and refuses redirects that downgrade to http or cross hosts (the
  Authorization header must never leave the original TLS origin).

Reads SOCIAL_ADMIN_EMAIL / SOCIAL_ADMIN_PASSWORD from repo .env.
API base: SOCIAL_API_URL or http://127.0.0.1:8083
"""
from __future__ import annotations

import hashlib
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


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects that downgrade to http or change host — the
    Authorization header must not leak off the original TLS origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        orig = urllib.parse.urlparse(req.full_url)
        new = urllib.parse.urlparse(newurl)
        if new.scheme != orig.scheme or new.hostname != orig.hostname:
            raise urllib.error.HTTPError(
                newurl, code, f"Refused unsafe redirect to {newurl}", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


OPENER = urllib.request.build_opener(SafeRedirect())


def check_api_url() -> None:
    parsed = urllib.parse.urlparse(API)
    if parsed.scheme not in {"http", "https"}:
        raise SystemExit(f"SOCIAL_API_URL must be http(s): {API}")
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
    raw = OPENER.open(r, timeout=90).read()
    return json.loads(raw) if raw else {}  # DELETE returns 204 empty


def login() -> str:
    email, password = load_env()
    r = urllib.request.Request(
        f"{API}/api/v1/auth/login",
        data=urllib.parse.urlencode({"username": email, "password": password}).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    return json.loads(OPENER.open(r, timeout=30).read())["access_token"]


def analyze(token: str, post: dict) -> dict:
    return req(
        "/api/v1/ai/analyze-content", token,
        {"content": post["text"], "platform": post["platform"]},
    )


def fingerprint(post: dict) -> str:
    """Stable hash of the schedulable fields — detects plan edits."""
    payload = json.dumps(
        {
            "text": post["text"],
            "scheduled_at": post["scheduled_at"],
            "account_ids": sorted(post["account_ids"]),
            "media_ids": sorted(post.get("media_ids") or []),
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


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


def scheduled_map(token: str) -> dict[tuple[str, str], str]:
    """(platform, content_text) -> post_id for everything scheduled,
    paginating the whole queue (list endpoint ignores `limit`)."""
    out: dict[tuple[str, str], str] = {}
    page, page_size = 1, 100
    while True:
        d = req(
            f"/api/v1/content/posts?status=scheduled&page={page}&page_size={page_size}",
            token, method="GET")
        items = d.get("posts") or d.get("items") or []
        for p in items:
            for t in p.get("post_targets") or p.get("targets") or []:
                out[(t.get("platform"), p.get("content_text"))] = p.get("id")
        total = d.get("total") or len(items)
        if page * page_size >= total or not items:
            break
        page += 1
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
    prior: dict[str, dict] = {}
    if created_file.exists():
        prior = json.loads(created_file.read_text()).get("post_ids") or {}

    token = login()
    print(f"Campaign: {plan.get('campaign', plan_file.stem)} — {len(posts)} posts [{mode}]")

    created_ids: dict[str, dict] = dict(prior)
    failures = 0

    def save_ledger() -> None:
        created_file.write_text(json.dumps(
            {"campaign": plan.get("campaign"), "post_ids": created_ids}, indent=1))

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
            entry = prior.get(key)
            # Ledger entries are {"id","fp"}; legacy files stored bare strings.
            pid = post.get("post_id") or (entry.get("id") if isinstance(entry, dict) else entry)
            if not isinstance(pid, str):
                print(f"{key}: no post_id recorded — FAILED")
                failures += 1
                continue
            d = req(f"/api/v1/content/posts/{pid}", token, method="GET")
            verbatim = d.get("content_text") == post["text"]
            pstatus = d.get("status")
            targets = d.get("post_targets") or d.get("targets") or []
            tinfo = [(t.get("platform"), t.get("status")) for t in targets]
            bad_t = [s for _, s in tinfo if s in {"failed", "skipped"}]
            print(f"{key}: status={pstatus} verbatim={verbatim} targets={tinfo}")
            if not verbatim or not targets or bad_t or pstatus in {"failed", "draft"}:
                if bad_t:
                    print(f"  !! {len(bad_t)} target(s) failed/skipped")
                failures += 1
            continue

        passed, reason = gate(token, post)
        print(f"{key}: {reason}")

        if mode == "schedule":
            if not passed:
                failures += 1
                continue
            if post.get("platform") == "instagram" and not post.get("media_ids"):
                print("  !! instagram requires media_ids — not creating")
                failures += 1
                continue
            fp = fingerprint(post)
            if key in created_ids:
                prev = created_ids[key]
                prev_fp = prev.get("fp") if isinstance(prev, dict) else None
                if prev_fp is None or prev_fp == fp:
                    pid = prev.get("id") if isinstance(prev, dict) else prev
                    print(f"  -> already created as {pid} — skipping")
                    continue
                print(f"  !! plan changed for existing key {key} "
                      f"(post {prev.get('id')}) — edit it via the API or delete "
                      "and re-run; not creating a duplicate")
                failures += 1
                continue
            existing_pid = scheduled_map(token).get((post["platform"], post["text"]))
            if existing_pid:
                print(f"  -> identical text already scheduled ({existing_pid}) — adopting")
                created_ids[key] = {"id": existing_pid, "fp": fp}
                save_ledger()
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
                created_ids[key] = {"id": pid, "fp": fp}
                save_ledger()  # persist before any later network call can abort
                d = req(f"/api/v1/content/posts/{pid}", token, method="GET")
                verbatim = d.get("content_text") == post["text"]
                targets = d.get("post_targets") or d.get("targets") or []
                ok = verbatim and d.get("status") == "scheduled" and targets
                print(f"  -> {pid} status={d.get('status')} verbatim={verbatim} "
                      f"targets={[(t.get('platform'), t.get('status')) for t in targets]}")
                if not ok:
                    req(f"/api/v1/content/posts/{pid}", token, method="DELETE")
                    created_ids.pop(key, None)
                    save_ledger()
                    print(f"  !! verbatim/target check failed — deleted {pid}")
                    failures += 1
            except urllib.error.HTTPError as e:
                print(f"  !! create failed: HTTP {e.code} {e.read()[:200]}")
                failures += 1

    if mode == "schedule" and created_ids:
        save_ledger()
        print(f"Post IDs saved to {created_file}")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
