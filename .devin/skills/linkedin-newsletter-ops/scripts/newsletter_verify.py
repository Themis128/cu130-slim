#!/usr/bin/env python3
"""Verify a LinkedIn newsletter issue actually went live — and how many times.

Companion to newsletter_recover.py (which gets a stuck issue to `published`).
This answers the question the DB row can't: is the article really on
LinkedIn, and did a flaky retry publish it MORE than once?

What it does, in order:

  1. Reads the issue row (status/title/article_url) via social-postgres.
  2. Hits the sidecar /health (no navigation) — needs a live session.
  3. GET /profile/activity -> resolves the member profile URL (this also
     proves the session is logged in end-to-end).
  4. /debug/navigate to the article_url -> eval the rendered <title>/h1 —
     a live article renders its own title; a dead/gated one renders a
     login wall or LinkedIn 404.
  5. /debug/navigate to <profile>/recent-activity/all/ -> eval a count of
     anchors pointing at the article's /pulse/ slug AND occurrences of the
     title text. One activity entry = one publish event. The same URL
     linked twice inside one card is normal (badge + title) — what we
     count is the DISTINCT number of activity cards / URL forms.

Usage (host):
    python3 newsletter_verify.py black-friday-offers-2026
    python3 newsletter_verify.py ISSUE_SLUG --json
    python3 newsletter_verify.py --url 'https://www.linkedin.com/pulse/…/'
                                  --title 'Black Friday for your business…'
    (url+title mode skips the DB lookup — checks any article)

Exit codes: 0 live, exactly one publish event · 1 live but duplicated
            · 2 not live / not published / sidecar broken.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time

PSQL = [
    "docker", "exec", "social-postgres",
    "psql", "-U", "social_user", "-d", "social_automation", "-tA",
]
SIDECAR = "linkedin-browser-sidecar"
NAV_TIMEOUT = 90          # page.goto on a cold tab can take a while
SETTLE_S = 6              # let the SPA paint before evaluating

EVAL_ARTICLE = (
    'return {url: location.href, title: document.title, '
    'h1: (document.querySelector("h1")||{}).innerText || "", '
    'loginWall: !!document.querySelector(".authwall, '
    'form[action*=checkpoint]")}'
)

# Count distinct /pulse/ hrefs for THIS article plus title occurrences.
# %SLUG% is the article's pulse slug, %TITLE% a safe-quoted title head.
EVAL_ACTIVITY = (
    'return {url: location.href, '
    'links: Array.from(document.querySelectorAll("a[href*=\'%SLUG%\']"))'
    '.map(a => a.href.split("?")[0]), '
    'titleHits: (document.body.innerText.match(/%TITLE%/g) || []).length}'
)


def sh(cmd: list[str], timeout: int = 60) -> str:
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return (out.stdout + out.stderr).strip()


def sql(q: str) -> str:
    return sh(PSQL + ["-c", q])


def sidecar_alive() -> bool:
    return bool(sh(["docker", "inspect", SIDECAR,
                    "--format", "{{.State.Running}}"]).startswith("true"))


def http(method: str, path: str, timeout: int = 20,
         body: dict | None = None) -> dict:
    """Call the sidecar over `docker exec` — the container IP is NOT
    routable from the WSL host (Docker Desktop doesn't bridge container
    networks into WSL), but 127.0.0.1:9225 works inside the container."""
    cmd = ["docker", "exec", SIDECAR, "curl", "-s", "-m", str(timeout),
           "-o", "-", "-w", "\\n%{http_code}", "-X", method,
           f"http://127.0.0.1:9225{path}"]
    if body is not None:
        cmd += ["-H", "Content-Type: application/json",
                "-d", json.dumps(body)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=timeout + 15)
        raw = out.stdout.strip()
        code = int(raw.rsplit("\n", 1)[-1].strip() or 0) if raw else 0
        payload = raw.rsplit("\n", 1)[0] if "\n" in raw else ""
        return {"ok": 200 <= code < 300, "status": code,
                "json": _try_json(payload.encode())}
    except subprocess.TimeoutExpired:
        return {"ok": False, "status": 0, "json": {"error": "timed out"}}
    except Exception as e:
        return {"ok": False, "status": 0, "json": {"error": str(e)}}


def _try_json(raw: bytes) -> dict:
    try:
        return json.loads(raw or b"{}")
    except Exception:
        return {"raw": raw[:300].decode(errors="replace")}


def navigate(url: str) -> dict:
    r = http("POST", "/debug/navigate", timeout=NAV_TIMEOUT,
             body={"url": url})
    if not r["ok"] or r["json"].get("status") != "ok":
        return {}
    time.sleep(SETTLE_S)
    return r["json"]


def evaluate(expr: str) -> dict:
    r = http("POST", "/debug/eval", timeout=60, body={"expr": expr})
    return r.get("json", {}).get("value") or {}


def pulse_slug(url: str) -> str:
    # …/pulse/<slug>/ → <slug>
    parts = url.split("?")[0].rstrip("/").split("/pulse/")
    return parts[1] if len(parts) == 2 else url.rstrip("/").rsplit("/", 1)[-1]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("issue", nargs="?", help="issue uuid or slug")
    ap.add_argument("--url", help="article URL (ad-hoc mode, skips DB)")
    ap.add_argument("--title", help="article title (ad-hoc mode)")
    ap.add_argument("--json", action="store_true", help="machine output")
    args = ap.parse_args()

    article_url, title, status = args.url, args.title, None

    # ── 1. issue row ──────────────────────────────────────────────────────
    if args.issue:
        row = sql(
            "SELECT status, coalesce(article_url,''), coalesce(title,''), "
            "       coalesce(verified_at::text,'') "
            "FROM newsletter_issues "
            f"WHERE slug='{args.issue}' OR id::text='{args.issue}' LIMIT 1"
        )
        if not row:
            sys.exit(f"issue not found: {args.issue}")
        status, article_url, title, verified_at = row.split("|", 3)
        if status != "published":
            print(f"issue status={status} — nothing to verify "
                  f"(use newsletter_recover.py)")
            return 2

    if not article_url:
        sys.exit("no article_url — pass --url or use a published issue")
    slug = pulse_slug(article_url)
    title_head = (title or "")[:40].replace("'", "").strip()

    out = {"article_url": article_url, "slug": slug,
           "issue_status": status}

    # ── 2. sidecar health ─────────────────────────────────────────────────
    if not sidecar_alive():
        print("sidecar container not running")
        return 2
    health = http("GET", "/health")
    if not health["ok"]:
        print(f"sidecar /health failed: {health['json']}")
        return 2
    h = health["json"]
    out["session"] = h.get("has_session")
    if not h.get("has_session"):
        print("sidecar has no session — heal it first "
              "(newsletter_recover.py --status / --relogin)")
        return 2
    if h.get("rate_limited"):
        print(f"circuit open until {h.get('rate_limit_until')} "
              f"({h.get('rate_limit_reason')}) — navigation may re-trip; "
              f"verify later")
        return 2

    # ── 3. profile URL (proves session) ───────────────────────────────────
    prof = http("GET", "/profile/activity", timeout=NAV_TIMEOUT)
    profile_url = (prof.get("json") or {}).get("profile_url")
    out["profile_url"] = profile_url
    if not profile_url:
        print("profile/activity could not resolve the profile — "
              "session looks dead")
        return 2

    # ── 4. article live check ─────────────────────────────────────────────
    nav = navigate(article_url)
    if not nav:
        print("navigate to article_url failed/timed out")
        return 2
    ev = evaluate(EVAL_ARTICLE)
    out["article"] = ev
    live = (not ev.get("loginWall")
            and title_head
            and title_head.split("—")[0].strip().split("|")[0].strip().lower()
            in (ev.get("title", "") + ev.get("h1", "")).lower())
    out["live"] = bool(live)

    # ── 5. duplicate scan on the activity feed ────────────────────────────
    activity = profile_url.rstrip("/") + "/recent-activity/all/"
    nav2 = navigate(activity)
    dup_events, feed_url = None, None
    if nav2:
        expr = (EVAL_ACTIVITY
                .replace("%SLUG%", slug)
                .replace("%TITLE%", title_head.replace(" ", "\\s+") or "zzz"))
        ev2 = evaluate(expr)
        feed_url = ev2.get("url")
        links = [x for x in ev2.get("links", []) if slug in x]
        # Distinct URL forms = distinct publish events (badge+title links
        # inside one card share the identical URL, so dedupe by URL).
        dup_events = len(set(links)) or None
        out["activity"] = {"url": feed_url,
                           "article_links": len(links),
                           "distinct_article_urls": sorted(set(links)),
                           "title_hits": ev2.get("titleHits")}
    out["publish_events"] = dup_events

    if args.json:
        print(json.dumps(out, indent=1))
        return 0 if live and (dup_events in (None, 1)) else (1 if live else 2)

    # ── verdict ───────────────────────────────────────────────────────────
    print(f"article_url : {article_url}")
    print(f"session     : {h.get('has_session')} "
          f"(profile {profile_url})")
    print(f"live render : {'YES' if live else 'NO'} — "
          f"title={ev.get('title', '?')[:80]!r}")
    if dup_events is None:
        print("duplicates  : unknown (activity page didn't load)")
    else:
        print(f"duplicates  : {dup_events} publish event(s) on the activity feed"
              + (" — CLEAN" if dup_events == 1 else " — DUPLICATED!"))
    if not live:
        return 2
    return 0 if dup_events in (None, 1) else 1


if __name__ == "__main__":
    sys.exit(main())
