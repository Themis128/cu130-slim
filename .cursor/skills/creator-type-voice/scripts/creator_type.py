#!/usr/bin/env python3
"""Creator Type voice tool — Visibility Era DAY 1 adoption.

Runs the Sofia Kakkava "Visibility Era" self-assessment (Creator Type:
Expert / Storyteller / Energizer), applies the resulting voice formula to
the SocialAuto brand voice (`brand_voices.voice_signature`), and verifies
that /api/v1/ai/generate-content produces posts in that style.

Stdlib-only; runs on the host. API auth: TOTP login against the admin
account (same flow as the n8n workflows and SocialAuto MCP).

Usage:
    creator_type.py show                    # current voice_signature
    creator_type.py quiz                    # interactive DAY 1 assessment
    creator_type.py apply <type>            # expert|storyteller|energizer|blend
    creator_type.py platforms               # DAY 2 platform tiers (live)
    creator_type.py verify [platform]       # generate a sample post
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request

API = os.environ.get("SOCIAL_API_URL", "http://localhost:8083").rstrip("/")

# ---------------------------------------------------------------------------
# DAY 1 assessment (Sofia Kakkava — Visibility Era Challenge)
# ---------------------------------------------------------------------------

TYPE_QUESTIONS = [
    ("When you want to help someone, you usually:",
     [("a", "Break it down step by step so they fully understand"),
      ("b", "Share a personal story that shows them they're not alone"),
      ("c", "Say something that fires them up and gets them moving")]),
    ("The content you enjoy consuming most is:",
     [("a", "Tutorials, how-tos, and detailed explanations"),
      ("b", "Real, raw, personal stories and behind-the-scenes"),
      ("c", "Bold opinions, motivational truths, energy-packed posts")]),
    ("People who know you would describe you as:",
     [("a", "The one who always explains things clearly"),
      ("b", "The one who always keeps it real"),
      ("c", "The one who always gets people excited")]),
]
TYPE_LETTERS = {"a": "expert", "b": "storyteller", "c": "energizer"}
TYPE_NAMES = {
    "expert": "The Expert — builds trust through knowledge",
    "storyteller": "The Storyteller — builds connection through honesty",
    "energizer": "The Energizer — builds momentum through boldness",
}

# Voice formulas written into brand_voices.voice_signature.
# Keys: creator_type, post_formula, style_notes.
TYPE_FORMULAS = {
    "expert": {
        "creator_type": "The Expert — builds trust through knowledge",
        "post_formula": (
            "Teach ONE concrete thing per post: a step-by-step breakdown, "
            "framework, or honest explanation the reader can use immediately. "
            "Open with the problem in one line, give the how in clear steps, "
            "close with an action-oriented question."
        ),
        "style_notes": (
            "Clarity over cleverness; every post is a mini-tutorial; plain "
            "everyday English; practitioner not guru; no hype words"
        ),
    },
    "storyteller": {
        "creator_type": "The Storyteller — builds connection through honesty",
        "post_formula": (
            "Anchor every post in something real: a true observation, client "
            "lesson, or behind-the-scenes detail — never invented anecdotes. "
            "Open with the moment or tension, tell what actually happened, "
            "land the honest takeaway, close with an inviting question."
        ),
        "style_notes": (
            "Feels personal and human; admits what didn't work; plain "
            "everyday English; specific details over abstractions"
        ),
    },
    "energizer": {
        "creator_type": "The Energizer — builds momentum through boldness",
        "post_formula": (
            "Lead with a bold, true statement or contrarian take. Back it "
            "with one concrete reason or proof point. Close with a call to "
            "action that creates momentum — do this today, not someday."
        ),
        "style_notes": (
            "Short punchy lines; confident but never arrogant; energy and "
            "conviction; plain everyday English"
        ),
    },
    "blend": {
        # Expert-led blend — the admin's actual DAY 1 result (A+B+C).
        "creator_type": (
            "Expert-led blend: Expert (breaks things down step-by-step) + "
            "Storyteller (real, honest stories) + Energizer (bold, "
            "action-driving energy)"
        ),
        "post_formula": (
            "1) Teach one concrete thing clearly (Expert: steps, framework, "
            "or honest explanation - no fluff). 2) Ground it in something "
            "real (Storyteller: a true observation, client lesson, or "
            "behind-the-scenes detail - never invented anecdotes). 3) Close "
            "with energizing momentum (Energizer: one bold line + "
            "action-oriented question inviting a reply)."
        ),
        "style_notes": (
            "Plain everyday English; explain like a practitioner not a "
            "guru; confident but never hypey; every post leaves the reader "
            "with something usable in under a minute"
        ),
    },
}

# ---------------------------------------------------------------------------
# DAY 2 — The 2-Platform Rule (8-week commitment)
#
# Owner's choice: MAIN = LinkedIn; SECONDARY = all Meta platforms;
# LAST = everything else. Written into voice_signature.platform_focus.
# ---------------------------------------------------------------------------

PLATFORM_TIERS = {
    "main": ["linkedin"],
    "secondary": ["instagram", "facebook", "threads"],
    "last": ["twitter", "tiktok"],
}
PLATFORM_FOCUS_TEXT = (
    "2-Platform Rule (8-week commitment): MAIN = LinkedIn (primary content, "
    "carousels, long-form Educator posts). SECONDARY = Meta platforms "
    "(Instagram, Facebook Page, Threads - adapted/cross-posted versions). "
    "LAST = everything else (Twitter/X, TikTok - opportunistic only, do not "
    "optimize for them)"
)

# ---------------------------------------------------------------------------
# API helpers (TOTP login — same flow as n8n workflows + socialauto MCP)
# ---------------------------------------------------------------------------

_TOKEN: str | None = None


def _get_token() -> str:
    global _TOKEN
    if _TOKEN:
        return _TOKEN
    code = """
import asyncio, httpx, os, base64, hashlib, hmac, struct, time
from sqlalchemy import select
from app.db.session import async_session_maker
from app.models.user import User
async def main():
    async with async_session_maker() as db:
        u = (await db.execute(select(User).where(
            User.email==os.environ['SOCIAL_ADMIN_EMAIL']))).scalar_one()
        secret = u.two_factor_secret
    key = base64.b32decode(secret)
    msg = struct.pack('>Q', int(time.time()) // 30)
    d = hmac.new(key, msg, hashlib.sha1).digest()
    o = d[-1] & 0x0F
    otp = str((struct.unpack('>I', d[o:o+4])[0] & 0x7FFFFFFF) % 10**6).zfill(6)
    async with httpx.AsyncClient() as c:
        r = await c.post('http://localhost:8000/api/v1/auth/login', data={
            'username': os.environ['SOCIAL_ADMIN_EMAIL'],
            'password': os.environ['SOCIAL_ADMIN_PASSWORD'], 'otp': otp})
        print(r.json()['access_token'])
asyncio.run(main())
"""
    r = subprocess.run(
        ["docker", "compose", "exec", "-T", "social-api", "python3", "-c", code],
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError(f"login failed: {r.stderr.strip()[:200]}")
    _TOKEN = r.stdout.strip()
    return _TOKEN


def _api(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{API}/api/v1{path}", data=data, method=method,
        headers={"Authorization": f"Bearer {_get_token()}",
                 "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode())


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_show() -> None:
    voice = _api("GET", "/brand/voice")
    print(json.dumps(voice.get("voice_signature", {}), indent=2))


def cmd_quiz() -> None:
    answers = []
    for i, (q, opts) in enumerate(TYPE_QUESTIONS, 1):
        print(f"\nQ{i}  {q}")
        for letter, text in opts:
            print(f"   {letter.upper()}  {text}")
        while True:
            a = input("   > ").strip().lower()[:1]
            if a in TYPE_LETTERS:
                answers.append(a)
                break
            print("   answer a, b, or c")
    counts = {t: answers.count(k) for k, t in TYPE_LETTERS.items()}
    top = max(counts.values())
    winners = [t for t, n in counts.items() if n == top]
    result = winners[0] if len(winners) == 1 else "blend"
    print(f"\nResult: {TYPE_NAMES.get(result, 'Mixed blend — Expert-led')}")
    print(f"Apply with: creator_type.py apply {result}")


def cmd_apply(kind: str) -> None:
    kind = kind.lower()
    if kind not in TYPE_FORMULAS:
        raise SystemExit(f"unknown type {kind!r} — use: "
                         + ", ".join(TYPE_FORMULAS))
    current = _api("GET", "/brand/voice").get("voice_signature", {}) or {}
    merged = {**current, **TYPE_FORMULAS[kind]}
    _api("PUT", "/brand/voice", {"voice_signature": merged})
    print(f"Applied {kind}:")
    print(json.dumps(TYPE_FORMULAS[kind], indent=2))


def cmd_platforms() -> None:
    sig = _api("GET", "/brand/voice").get("voice_signature", {}) or {}
    live = sig.get("platform_focus", "")
    for tier, plats in PLATFORM_TIERS.items():
        print(f"{tier.upper():10} {', '.join(plats)}")
    print()
    print("voice_signature.platform_focus:")
    print(" ", live or "(not set — run apply)")
    if live != PLATFORM_FOCUS_TEXT:
        print("\nNOTE: live text differs from PLATFORM_FOCUS_TEXT")


def cmd_verify(platform: str = "linkedin") -> None:
    res = _api("POST", "/ai/generate-content", {
        "prompt": "Why small teams waste money on servers they don't need",
        "platform": platform, "tone": "professional",
        "length": "short", "include_hashtags": True, "include_emojis": False,
    })
    content = res.get("content") or res.get("text") or json.dumps(res)[:800]
    print(content)


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd = sys.argv[1]
    if cmd == "show":
        cmd_show()
    elif cmd == "quiz":
        cmd_quiz()
    elif cmd == "apply" and len(sys.argv) > 2:
        cmd_apply(sys.argv[2])
    elif cmd == "platforms":
        cmd_platforms()
    elif cmd == "verify":
        cmd_verify(sys.argv[2] if len(sys.argv) > 2 else "linkedin")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
