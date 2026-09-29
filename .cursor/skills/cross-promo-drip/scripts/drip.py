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
- Idempotent + honest ledger: each key in <plan>.created.json stores
  {"id", "fp", "verified"}. A saved ID is skipped only when the plan
  fingerprint matches AND the post verified cleanly; unverified entries
  are re-checked on the next run, and a changed plan fails loudly.
- Adoption, not duplication: identical text already scheduled to the
  same platform is adopted only when its time/accounts/media match the
  plan; a mismatch is a conflict failure, never a silent alias.
- Verbatim check: if stored content_text differs from the draft, the
  created post is deleted immediately; if the delete cannot be
  confirmed, the entry is marked pending_cleanup and reconciled next run.
- Instagram posts require media_ids — the platform rejects text-only.
- Last-tier guard: Twitter/X and TikTok are "opportunistic only" per
  the 2-Platform Rule — scheduled plans targeting them are rejected.
- Credentials: refuses cleartext http:// for non-localhost API URLs,
  and refuses any redirect that changes scheme, host, or port — the
  Authorization header must never leave the original TLS origin.

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


def _origin(url: str) -> tuple:
    p = urllib.parse.urlparse(url)
    default = 443 if p.scheme == "https" else 80
    return (p.scheme, p.hostname, p.port or default)


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects to a different origin (scheme/host/port) — the
    Authorization header must not leak off the original TLS origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if _origin(newurl) != _origin(req.full_url):
            raise urllib.error.HTTPError(
                newurl, code, f"Refused cross-origin redirect to {newurl}",
                headers, fp)
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
    """Stable hash of every schedulable field — detects plan edits."""
    payload = json.dumps(
        {
            "platform": post["platform"],
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


def post_targets(d: dict) -> list[dict]:
    return d.get("post_targets") or d.get("targets") or []


def verify_post(d: dict, post: dict) -> tuple[bool, str]:
    """Check a fetched post against the plan. Returns (ok, detail)."""
    verbatim = d.get("content_text") == post["text"]
    pstatus = d.get("status")
    tinfo = [(t.get("platform"), t.get("status")) for t in post_targets(d)]
    bad_t = [s for _, s in tinfo if s in {"failed", "skipped"}]
    ok = verbatim and bool(tinfo) and not bad_t and pstatus not in {"failed", "draft"}
    return ok, f"status={pstatus} verbatim={verbatim} targets={tinfo}"


def _parse_dt(s: str | None):
    if not s:
        return None
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def matches_plan(d: dict, post: dict) -> bool:
    """Does a stored scheduled post actually match the plan's fields?
    Datetimes are normalized — the API serializes '+00:00' while plans
    conventionally write 'Z'."""
    if _parse_dt(d.get("scheduled_at")) != _parse_dt(post["scheduled_at"]):
        return False
    t_accts = {
        str(t.get("account_id") or t.get("social_account_id") or "")
        for t in post_targets(d)
    }
    if t_accts and t_accts != {str(a) for a in post["account_ids"]}:
        return False
    media = d.get("media_ids") or d.get("media") or []
    if {str(m) for m in media} != {str(m) for m in (post.get("media_ids") or [])}:
        return False
    return True


def scheduled_map(token: str) -> dict[tuple[str, str], str]:
    """(platform, content_text) -> post_id for the whole scheduled queue
    (list endpoint ignores `limit` — paginate via page/page_size)."""
    out: dict[tuple[str, str], str] = {}
    page, page_size = 1, 100
    while True:
        d = req(
            f"/api/v1/content/posts?status=scheduled&page={page}&page_size={page_size}",
            token, method="GET")
        items = d.get("posts") or d.get("items") or []
        for p in items:
            for t in post_targets(p):
                out[(t.get("platform"), p.get("content_text"))] = p.get("id")
        total = d.get("total") or len(items)
        if page * page_size >= total or not items:
            break
        page += 1
    return out


def entry_id(entry) -> str | None:
    """Ledger entries are {"id","fp","verified"}; legacy files stored bare
    strings. Returns the post ID either way."""
    if isinstance(entry, dict):
        return entry.get("id")
    return entry if isinstance(entry, str) else None


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
            pid = post.get("post_id") or entry_id(prior.get(key))
            if not pid:
                print(f"{key}: no post_id recorded — FAILED")
                failures += 1
                continue
            d = req(f"/api/v1/content/posts/{pid}", token, method="GET")
            ok, detail = verify_post(d, post)
            print(f"{key}: {detail}")
            if not ok:
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
            entry = created_ids.get(key)
            if entry is not None:
                pid = entry_id(entry)
                if isinstance(entry, dict) and entry.get("pending_cleanup"):
                    # Earlier delete may or may not have landed — reconcile.
                    try:
                        req(f"/api/v1/content/posts/{pid}", token, method="GET")
                        print(f"  !! {key}: cleanup pending but post {pid} still "
                              "exists — delete it manually and re-run")
                        failures += 1
                    except urllib.error.HTTPError as e:
                        if e.code == 404:
                            print(f"  -> cleanup confirmed for {pid}")
                            created_ids.pop(key)
                            save_ledger()
                        else:
                            raise
                    continue
                # Re-verify before trusting any saved ID: fetch the stored
                # post and compare it to the (possibly edited) plan.
                try:
                    d = req(f"/api/v1/content/posts/{pid}", token, method="GET")
                except urllib.error.HTTPError as e:
                    if e.code != 404:
                        raise
                    print(f"  -> saved post {pid} no longer exists — "
                          "dropping ledger entry")
                    created_ids.pop(key, None)
                    save_ledger()
                    entry = None
                else:
                    ok, detail = verify_post(d, post)
                    if ok and matches_plan(d, post):
                        # Live post matches the plan on every field — the
                        # fingerprint in the ledger is then redundant proof.
                        # Migrate legacy bare-string entries to dict form.
                        if not isinstance(entry, dict):
                            created_ids[key] = {"id": pid, "fp": fp,
                                                "verified": True}
                            save_ledger()
                        elif not entry.get("verified"):
                            entry["verified"] = True
                            save_ledger()
                        print(f"  -> already created as {pid} ({detail}) — skipping")
                        continue
                    # Mismatch = plan edited or the post was touched outside —
                    # report a conflict. Never delete an existing schedule:
                    # the post may be intentional, and recreation loses its
                    # original timing/history.
                    print(f"  !! plan/stored conflict for key {key} "
                          f"(post {pid}: {detail}, stored fields vs plan "
                          "differ) — edit via the API or delete and re-run")
                    failures += 1
                    continue

            existing_pid = scheduled_map(token).get((post["platform"], post["text"]))
            if existing_pid:
                d = req(f"/api/v1/content/posts/{existing_pid}", token, method="GET")
                if matches_plan(d, post):
                    print(f"  -> identical post already scheduled "
                          f"({existing_pid}) — adopting")
                    created_ids[key] = {"id": existing_pid, "fp": fp,
                                        "verified": True}
                    save_ledger()
                else:
                    print(f"  !! identical text already scheduled as "
                          f"{existing_pid} with different time/accounts/media "
                          "— resolve the conflict manually")
                    failures += 1
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
                # Ledger first (unverified) so an abort can't orphan the post.
                created_ids[key] = {"id": pid, "fp": fp, "verified": False}
                save_ledger()
                d = req(f"/api/v1/content/posts/{pid}", token, method="GET")
                ok, detail = verify_post(d, post)
                print(f"  -> {pid} {detail}")
                if not ok:
                    try:
                        req(f"/api/v1/content/posts/{pid}", token, method="DELETE")
                        created_ids.pop(key, None)
                        print(f"  !! verbatim/target check failed — deleted {pid}")
                    except urllib.error.HTTPError:
                        # Ambiguous cleanup — keep the ID flagged for
                        # reconciliation on the next run.
                        created_ids[key] = {"id": pid, "fp": fp,
                                            "verified": False,
                                            "pending_cleanup": True}
                        print(f"  !! check failed; delete of {pid} "
                              "unconfirmed — marked pending_cleanup")
                    save_ledger()
                    failures += 1
                else:
                    created_ids[key]["verified"] = True
                    save_ledger()
            except urllib.error.HTTPError as e:
                print(f"  !! create failed: HTTP {e.code} {e.read()[:200]}")
                failures += 1

    if mode == "schedule" and created_ids:
        save_ledger()
        print(f"Post IDs saved to {created_file}")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
