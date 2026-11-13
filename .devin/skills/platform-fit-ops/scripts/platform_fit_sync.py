#!/usr/bin/env python3
"""platform_fit_sync.py — regenerate every surface that carries the
per-platform audience/algorithm content-fit knowledge.

Single source of truth:
    social-automation/backend/app/services/platform_fit.json

Surfaces kept in sync:
    1. PLATFORM_GUIDES — automatically, at import time (content_guides.py
       appends each platform's ``guide_line``). Nothing to do here; verify
       with ``--verify-guides``.
    2. Channel skills — marker blocks ``<!-- PLATFORM-FIT:BEGIN <key> -->``
       inside ``## Content rules`` of each ``*-content-ops/SKILL.md`` in
       both ``.devin/skills/`` and ``.cursor/skills/``. Legacy hand-written
       "### What the ... favors" sections are converted to markers on first
       run.
    3. Company brain — ``--brain`` upserts doc ``platform-fit-reference``
       into the team's Chroma ``team_*_brain`` collection and the
       "Platform Content Fit — Reference" Nextcloud note (category
       "Company Brain") via ``app.services.company_brain`` inside
       ``social-api``.

Usage:
    platform_fit_sync.py            # rewrite skill blocks
    platform_fit_sync.py --check    # report drift, write nothing (exit 1)
    platform_fit_sync.py --brain    # also seed the company brain
    platform_fit_sync.py --verify-guides  # print resolved PLATFORM_GUIDES fit lines
    platform_fit_sync.py --ingest <src> [--no-brain] [--commit]
        # src = file path, '-' (stdin), or http(s) URL. Runs the research
        # text through the stack's LLM chain (DMR -> Cloudflare fallback,
        # inside social-api), merges the extracted per-platform entries
        # into platform_fit.json, rewrites all surfaces, seeds the brain.
        # --commit stages the touched repo files on the current branch.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

# PLATFORM_FIT_REPO lets the weekly auto-refresh run this script inside a
# clean `git worktree` (origin/master) instead of the live checkout.
REPO = Path(os.environ.get("PLATFORM_FIT_REPO") or Path(__file__).resolve().parents[4])
FIT_JSON = REPO / "social-automation/backend/app/services/platform_fit.json"

SKILL_SLUG = {
    "linkedin": "linkedin-content-ops",
    "twitter": "x-content-ops",
    "instagram": "instagram-content-ops",
    "facebook": "facebook-content-ops",
    "threads": "threads-content-ops",
    "tiktok": "tiktok-content-ops",
    "bluesky": "bluesky-content-ops",
}
SKILL_DIRS = (".devin/skills", ".cursor/skills")

DISPLAY = {
    "linkedin": "LinkedIn",
    "twitter": "X/Twitter",
    "instagram": "Instagram",
    "facebook": "Facebook",
    "threads": "Threads",
    "tiktok": "TikTok",
    "bluesky": "Bluesky",
}

MARKER_RE = lambda key: re.compile(  # noqa: E731
    rf"<!-- PLATFORM-FIT:BEGIN {key} -->.*?<!-- PLATFORM-FIT:END -->",
    re.DOTALL,
)
# Legacy hand-written fit sections: heading + bullet lines, where a bullet
# may wrap onto indented continuation lines. Stops at the first blank line.
LEGACY_RE = re.compile(r"### What the .*?favors\n(?:(?:- |[ \t]{2,})[^\n]*\n)+\n?", re.DOTALL)


def load_fit() -> dict:
    data = json.loads(FIT_JSON.read_text())
    return {k: v for k, v in data.items() if not k.startswith("_")}


def block_for(key: str, entry: dict) -> str:
    bullets = "\n".join(f"- {b}" for b in entry["bullets"])
    return (
        f"<!-- PLATFORM-FIT:BEGIN {key} -->\n"
        f"### {entry['skill_title']}\n"
        f"{bullets}\n\n"
        "<!-- PLATFORM-FIT:END -->"
    )


def skill_files(key: str) -> list[Path]:
    slug = SKILL_SLUG[key]
    return [REPO / d / slug / "SKILL.md" for d in SKILL_DIRS]


def sync_file(path: Path, key: str, entry: dict, check: bool) -> str:
    """Return 'changed'|'ok'|'missing'."""
    if not path.exists():
        return "missing"
    text = path.read_text()
    block = block_for(key, entry)
    new, n = MARKER_RE(key).subn(block, text)
    if n == 0:
        new, n = LEGACY_RE.subn(block + "\n", text, count=1)
    if n == 0:
        anchor = re.search(r"^## Content rules\s*$", new, re.MULTILINE)
        if not anchor:
            return "no-anchor"
        pos = anchor.end()
        new = new[:pos] + "\n" + block + "\n" + new[pos:]
    if new == text:
        return "ok"
    if not check:
        path.write_text(new)
    return "changed"


def brain_markdown(fit: dict) -> str:
    lines = [
        "# Platform Content Fit — audience mindset reference",
        "",
        "Canonical source: `social-automation/backend/app/services/platform_fit.json`",
        "(regenerate surfaces with `.devin/skills/platform-fit-ops/scripts/platform_fit_sync.py`).",
        "",
    ]
    for key, entry in fit.items():
        lines.append(f"## {DISPLAY.get(key, key)}")
        lines.extend(f"- {b}" for b in entry["bullets"])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def resolve_team_id() -> str:
    if os.environ.get("SOCIALAUTO_TEAM_ID"):
        return os.environ["SOCIALAUTO_TEAM_ID"]
    r = _run(
        [
            "docker", "exec", "social-postgres", "psql", "-U", "social_user",
            "-d", "social_automation", "-tAc",
            "SELECT id FROM teams WHERE name ILIKE '%baltzakis%' LIMIT 1",
        ]
    )
    tid = r.stdout.strip()
    if r.returncode != 0 or not tid:
        sys.exit("platform_fit_sync: could not resolve team_id "
                 "(set SOCIALAUTO_TEAM_ID or check social-postgres)")
    return tid


def seed_brain(fit: dict) -> bool:
    team_id = resolve_team_id()
    payload = json.dumps({
        "team_id": team_id,
        "text": brain_markdown(fit),
        # Non-ASCII must ride the env payload (json escapes it) — a literal
        # em-dash inside the -c code gets mangled to ??? under POSIX locale.
        "note_title": "Platform Content Fit — Reference",
    })
    code = (
        "import asyncio, json, os\n"
        "from app.services.company_brain import remember, _write_nextcloud_note\n"
        "p = json.loads(os.environ['PF_PAYLOAD'])\n"
        "async def main():\n"
        "    m = await remember(p['team_id'], 'platform-fit-reference', p['text'],\n"
        "                        metadata={'kind': 'platform-fit', 'day': 'reference'})\n"
        "    n = await _write_nextcloud_note(p['note_title'], p['text'])\n"
        "    print('memory:', m, 'note:', n)\n"
        "asyncio.run(main())\n"
    )
    env = dict(os.environ, PF_PAYLOAD=payload)
    r = subprocess.run(
        ["docker", "exec", "-i", "-e", "PF_PAYLOAD", "social-api", "python3", "-c", code],
        capture_output=True, text=True, env=env,
    )
    print(r.stdout.strip() or r.stderr.strip()[-500:])
    return r.returncode == 0 and "memory: True" in r.stdout


INGEST_PROMPT = """You extract social-platform content-fit knowledge into strict JSON.

Given the research text below, output a JSON object keyed ONLY by the
platforms the text actually covers. Valid keys: linkedin, twitter
(use "twitter" for X/Twitter), instagram, facebook, threads, tiktok.
Omit every platform the text does not discuss — do not guess or pad.

Each value is an object:
- "guide_line": ONE concise sentence (<=200 chars) stating what that
  platform's audience/algorithm rewards, as a noun phrase continuing
  "Audience fit: ..." (e.g. "thought leadership, educational carousels, ...").
- "skill_title": a markdown H3 title like "What the LinkedIn
  audience/algorithm favors".
- "bullets": 1-4 single-line strings capturing the concrete
  formats/tactics that perform on that platform.

Rules: no invented metrics; ignore pure summary tables unless they add
facts; output ONLY the JSON object — no markdown fences, no commentary.

RESEARCH TEXT:
"""


FEEDS = [
    "https://www.socialmediaexaminer.com/feed/",
    "https://blog.hootsuite.com/feed/",
    "https://buffer.com/resources/rss/",
    "https://sproutsocial.com/insights/feed/",
]


def _strip_html(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def fetch_feeds(max_items: int = 15) -> str:
    """Pull the latest items from the platform-marketing RSS feeds."""
    chunks = []
    for url in FEEDS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "platform-fit-refresh/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                xml = r.read().decode("utf-8", "replace")
        except Exception as exc:
            print(f"feed skip {url}: {exc}")
            continue
        items = re.findall(r"<item>(.*?)</item>", xml, re.DOTALL)[:max_items]
        for it in items:
            title = _strip_html(re.search(r"<title>(.*?)</title>", it, re.DOTALL).group(1)) \
                if re.search(r"<title>(.*?)</title>", it, re.DOTALL) else ""
            desc_m = re.search(r"<description>(.*?)</description>", it, re.DOTALL) \
                or re.search(r"<content:encoded>(.*?)</content:encoded>", it, re.DOTALL)
            desc = _strip_html(desc_m.group(1)) if desc_m else ""
            if title or desc:
                chunks.append(f"## {title}\n{desc[:1500]}")
        print(f"feed ok {url}: {len(items)} items")
    return "\n\n".join(chunks)


def read_ingest_source(src: str) -> str:
    if src == "-":
        return sys.stdin.read()
    if src.startswith(("http://", "https://")):
        import urllib.request

        with urllib.request.urlopen(src, timeout=30) as r:
            return r.read().decode("utf-8", "replace")
    return Path(src).read_text()


def llm_extract(text: str) -> dict:
    """Run the ingest extraction through the stack LLM chain in social-api."""
    snippet = (
        "import asyncio, json, os\n"
        "from app.services.inference import call_inference\n"
        "async def main():\n"
        "    r = await call_inference(\n"
        "        os.environ['PF_INGEST_PROMPT'] + os.environ['PF_INGEST_TEXT'],\n"
        "        provider_name='dmr', allow_fallback=True, max_tokens=3000)\n"
        "    print(json.dumps(r))\n"
        "asyncio.run(main())\n"
    )
    env = dict(
        os.environ,
        PF_INGEST_PROMPT=INGEST_PROMPT,
        PF_INGEST_TEXT=text[:12000],
    )
    r = subprocess.run(
        ["docker", "exec", "-i", "-e", "PF_INGEST_PROMPT", "-e", "PF_INGEST_TEXT",
         "social-api", "python3", "-c", snippet],
        capture_output=True, text=True, env=env, timeout=600,
    )
    if r.returncode != 0:
        sys.exit(f"platform_fit_sync: LLM extraction failed:\n{r.stderr.strip()[-800:]}")
    out = json.loads(r.stdout.strip().splitlines()[-1])
    # call_inference returns {"text": ...} for schema-less calls — pull the
    # JSON object out of whatever wrapper/text came back.
    candidates = [out.get("json"), out.get("result")]
    text_out = out.get("text") if isinstance(out, dict) else None
    if isinstance(text_out, str):
        m = re.search(r"\{.*\}", text_out, re.DOTALL)
        if m:
            try:
                candidates.append(json.loads(m.group(0)))
            except json.JSONDecodeError:
                pass
    candidates.append(out)
    for cand in candidates:
        if isinstance(cand, dict) and any(k in cand for k in SKILL_SLUG):
            return {k: v for k, v in cand.items() if k in SKILL_SLUG}
    sys.exit(f"platform_fit_sync: LLM returned no platform entries: {str(out)[:300]}")


def merge_fit(fit: dict, extracted: dict) -> list[str]:
    changed = []
    for key, entry in extracted.items():
        if not isinstance(entry, dict):
            continue
        gl, st, bl = entry.get("guide_line"), entry.get("skill_title"), entry.get("bullets")
        if not (isinstance(gl, str) and gl.strip() and isinstance(st, str) and st.strip()
                and isinstance(bl, list) and bl and all(isinstance(b, str) and b.strip() for b in bl)):
            print(f"  skip {key}: malformed entry")
            continue
        fit[key] = {"guide_line": gl.strip(), "skill_title": st.strip(),
                    "bullets": [b.strip() for b in bl]}
        changed.append(key)
    return changed


def sync_all(fit: dict, check: bool) -> int:
    drift = 0
    for key, entry in fit.items():
        for path in skill_files(key):
            status = sync_file(path, key, entry, check=check)
            if status in ("changed", "no-anchor"):
                drift += 1
            print(f"{status:8} {path.relative_to(REPO)}")
    return drift


def git_commit(touched: list[Path]) -> bool:
    import subprocess as sp

    files = [str(p.relative_to(REPO)) for p in touched]
    sp.run(["git", "add", *files], cwd=REPO, check=True)
    r = sp.run(
        ["git", "commit", "-q", "-m",
         "content: platform-fit ingest update\n\n"
         "Generated with [Devin](https://devin.ai)\n\n"
         "Co-Authored-By: Devin <158243242+devin-ai-integration[bot]@users.noreply.github.com>"],
        cwd=REPO, capture_output=True, text=True,
    )
    print(r.stdout.strip() or r.stderr.strip())
    return r.returncode == 0


def verify_guides() -> int:
    code = (
        "from app.services.content_guides import PLATFORM_GUIDES\n"
        "for k in ('linkedin','twitter','instagram','facebook','threads','tiktok','bluesky'):\n"
        "    g = PLATFORM_GUIDES.get(k,'')\n"
        "    i = g.find('Audience fit:')\n"
        "    print(k, '->', g[i:i+110] if i >= 0 else 'MISSING')\n"
    )
    r = subprocess.run(
        ["docker", "exec", "-i", "social-api", "python3", "-c", code],
        capture_output=True, text=True,
    )
    print(r.stdout or r.stderr)
    return r.returncode


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="report drift without writing")
    ap.add_argument("--brain", action="store_true", help="also upsert the company-brain doc + Nextcloud note")
    ap.add_argument("--verify-guides", action="store_true", help="print resolved PLATFORM_GUIDES fit lines and exit")
    ap.add_argument("--ingest", metavar="SRC", help="research text file, '-' for stdin, or URL — LLM-extract into platform_fit.json")
    ap.add_argument("--ingest-feeds", action="store_true", help="fetch the built-in platform-marketing RSS feeds and ingest them (used by the weekly auto-refresh)")
    ap.add_argument("--no-brain", action="store_true", help="with --ingest: skip the company-brain seed")
    ap.add_argument("--commit", action="store_true", help="with --ingest: git-commit the touched files on the current branch")
    args = ap.parse_args()

    if not FIT_JSON.exists():
        sys.exit(f"platform_fit_sync: {FIT_JSON} not found — platform-fit-ops "
                 "is not on this branch yet (PR pending merge?)")
    fit = load_fit()
    if args.verify_guides:
        return verify_guides()

    if args.ingest or args.ingest_feeds:
        # ~6 items/feed keeps the prompt ~7k chars — the sweet spot for the
        # small local models (15/feed degenerated into generic filler).
        text = fetch_feeds(max_items=6) if args.ingest_feeds else read_ingest_source(args.ingest)
        if not text.strip():
            sys.exit("platform_fit_sync: empty ingest source")
        extracted = llm_extract(text)
        changed = merge_fit(fit, extracted)
        if not changed:
            sys.exit("platform_fit_sync: nothing extracted — platform_fit.json unchanged")
        raw = json.loads(FIT_JSON.read_text())
        raw.update(fit)
        FIT_JSON.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n")
        print(f"merged platforms: {', '.join(changed)}")
        touched = [FIT_JSON]
        for key in changed:
            for path in skill_files(key):
                if sync_file(path, key, fit[key], check=False) == "changed":
                    touched.append(path)
        if not args.no_brain:
            ok = seed_brain(fit)
            print(f"brain seed: {'ok' if ok else 'FAILED'}")
        if args.commit:
            git_commit(touched)
        return 0

    drift = sync_all(fit, check=args.check)

    if args.check:
        print(f"\n{drift} file(s) drifted" if drift else "\nall surfaces in sync")
        return 1 if drift else 0

    if args.brain:
        ok = seed_brain(fit)
        print(f"brain seed: {'ok' if ok else 'FAILED'}")
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
