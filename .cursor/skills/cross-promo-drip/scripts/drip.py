#!/usr/bin/env python3
"""Cross-promotion drip scheduler for SocialAuto.

Analyzes each planned copy through /api/v1/ai/analyze-content, then
optionally creates scheduled posts and verifies verbatim storage.

Usage:
    python3 drip.py plan.json              # analyze only (dry run)
    python3 drip.py plan.json --schedule   # analyze + create scheduled posts
    python3 drip.py plan.json --verify     # re-check previously created posts

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

Reads SOCIAL_ADMIN_EMAIL / SOCIAL_ADMIN_PASSWORD from repo .env.
API base: SOCIAL_API_URL or http://127.0.0.1:8083
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
API = os.environ.get("SOCIAL_API_URL", "http://127.0.0.1:8083")
MIN_SEO = 90


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


def main() -> int:
    plan_file = Path(sys.argv[1])
    mode = "analyze"
    if "--schedule" in sys.argv:
        mode = "schedule"
    elif "--verify" in sys.argv:
        mode = "verify"

    plan = json.loads(plan_file.read_text())
    token = login()
    print(f"Campaign: {plan.get('campaign', plan_file.stem)} — {len(plan['posts'])} posts [{mode}]")

    created_ids = {}
    failures = 0

    for post in plan["posts"]:
        key = post["key"]

        if mode == "verify":
            pid = post.get("post_id") or created_ids.get(key)
            if not pid:
                print(f"{key}: no post_id recorded — skipping")
                continue
            d = req(f"/api/v1/content/posts/{pid}", token, method="GET")
            verbatim = d.get("content_text") == post["text"]
            targets = d.get("post_targets") or d.get("targets") or []
            tinfo = [(t.get("platform"), t.get("status")) for t in targets]
            print(f"{key}: status={d.get('status')} verbatim={verbatim} targets={tinfo}")
            if not verbatim:
                failures += 1
            continue

        rep = analyze(token, post)
        seo = (rep.get("seo_score") or {}).get("overall")
        spell = len(rep.get("spellcheck_issues") or [])
        pe = rep.get("plain_english_issues") or []
        recs = ((rep.get("seo_score") or {}).get("recommendations") or [])[:3]
        print(f"{key}: seo={seo} spell={spell} pe={len(pe)} {recs if recs else ''}")

        if mode == "schedule":
            if seo is not None and seo < MIN_SEO:
                print(f"  !! below {MIN_SEO} — fix copy and re-run (not creating)")
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
                created_ids[key] = pid
                # immediate verbatim + target check
                d = req(f"/api/v1/content/posts/{pid}", token, method="GET")
                verbatim = d.get("content_text") == post["text"]
                targets = d.get("post_targets") or d.get("targets") or []
                ok = verbatim and d.get("status") == "scheduled" and targets
                print(f"  -> {pid} status={d.get('status')} verbatim={verbatim} "
                      f"targets={[(t.get('platform'), t.get('status')) for t in targets]}")
                if not ok:
                    failures += 1
            except urllib.error.HTTPError as e:
                print(f"  !! create failed: HTTP {e.code} {e.read()[:200]}")
                failures += 1

    if created_ids:
        out = plan_file.with_suffix(".created.json")
        out.write_text(json.dumps({"campaign": plan.get("campaign"), "post_ids": created_ids}, indent=1))
        print(f"Post IDs saved to {out}")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
