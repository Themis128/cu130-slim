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
    creator_type.py blueprint [apply]       # DAY 12 post blueprint (live)
    creator_type.py day13 [apply]           # DAY 13 leading content (live)
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
# DAY 12 — The Post Blueprint (4-part structure every post follows)
#
# Written into voice_signature.post_blueprint. Orthogonal to post_formula:
# the blueprint is the skeleton (hook → context → value → CTA), the creator
# type formula is the flavor inside it.
# ---------------------------------------------------------------------------

DAY12_BLUEPRINT = (
    "Post Blueprint (Visibility Era DAY 12) - structure every post in 4 "
    "parts, in this order: 1) HOOK - the very first line stops the scroll: "
    "a specific situation the reader recognizes themselves in, never reveal "
    "the answer up front. 2) CONTEXT - 2-3 lines that make the reader feel "
    "seen; show you understand their situation before teaching anything. "
    "3) VALUE - ONE clear insight fully explained in 3-5 lines; never a "
    "list of tips, one thing lands harder than five scattered. 4) CTA - one "
    "specific question or direction, max 2 lines; invite conversation, "
    "don't push. Every line must earn the next one - if a line would be "
    "skipped, cut it."
)

DAY12_MISTAKES = (
    "Blueprint mistakes to avoid: skipping the context (reader isn't ready "
    "for value yet), more than one point in the value section, a vague CTA "
    "like 'let me know what you think', burying the hook ('I've been "
    "thinking...' openers), and writing for yourself instead of the reader."
)

# ---------------------------------------------------------------------------
# DAY 13 — Content That Leads + One Script, Any Format
#
# Two Day-13 source docs (3 week/Day13/): "Write Content That Leads"
# (the 3-part script) and "Your Script, Any Format" (the same script
# rendered as written post / carousel / talking head / reel).
# Written into voice_signature.content_that_leads (upgraded from the
# one-liner), .day13_formats, .day13_prompts.
# ---------------------------------------------------------------------------

DAY13_LEADS = (
    "DAY 13 — Content That Leads. Information informs; leadership moves. "
    "Every post is a 3-part journey, not an info dump: 1) WHERE THEY ARE — "
    "name the exact situation the reader is stuck in right now (not where "
    "they want to be) so they feel completely seen before anything else. "
    "2) WHAT SHIFTS — ONE insight that reframes the situation: not a list "
    "of tips, not advice, one clear shift. 3) WHERE THEY GO — paint what "
    "becomes possible after the shift, then close with one specific "
    "question or direction as the CTA. A leading post ends differently "
    "than it starts — the reader feels like they already moved. Script "
    "mistakes that break it: lingering in part 1, skipping the shift, "
    "cramming three insights into one, ending with no direction, teaching "
    "instead of leading. Every line should earn the next one."
)

# The same 3-part script compressed per format — the structure never
# changes, only the delivery. Carousel + reel drive the media pipelines.
DAY13_FORMATS = {
    "written_post": (
        "Line 1 = hook only, no answer. Lines 2-4 = context, one sentence "
        "per line, make them feel seen — don't teach yet. Value = 3-5 "
        "lines, the ONE shift fully explained, never rushed to the CTA. "
        "CTA = 1-2 lines, one specific question. Best for "
        "LinkedIn/IG/FB/Threads; lowest barrier, test ideas here first."
    ),
    "carousel": (
        "Slide 1 = hook alone, impossible to ignore. Slides 2-3 = where "
        "they are, one idea per slide, make them feel seen. Slides 4-5 = "
        "the shift, big text, short sentences, let it breathe. Slide 6 = "
        "where they go, brief and vivid. Last slide = ONE CTA question — "
        "never two CTAs on one slide. Highest save rate; best on "
        "Instagram + LinkedIn."
    ),
    "talking_head": (
        "0:00-0:10 = hook spoken out loud, no intro, no 'hey guys', jump "
        "into their world — first 3 seconds decide everything. "
        "0:10-0:40 = the shift, one idea, speak slowly, pause, let it "
        "land. 0:40-1:00 = where they go + CTA spoken naturally, not like "
        "a sales pitch. Builds trust fastest."
    ),
    "reel": (
        "15-60s. Same script delivered as TEXT OVERLAY on b-roll (hands "
        "typing, coffee, workspace, behind the scenes) — no face "
        "recording needed. 0-3s = hook text, one line. 3-20s = the "
        "shift, short sentences appearing one at a time. 20-40s = the "
        "picture over aspirational b-roll. 40-60s = CTA as an on-screen "
        "question. Highest reach/discovery of all formats."
    ),
}

# Sofia's two ready-made AI prompts — a generator and a draft reviewer.
# Workflows can call these verbatim; the checker doubles as a QA gate.
DAY13_PROMPTS = {
    "write_script": (
        "My topic is [TOPIC]. My audience is [WHO THEY ARE — be specific "
        "about their current situation and struggle]. Write a post using "
        "this 3-part script: Part 1 — Where they are: name the exact "
        "situation they are stuck in right now; make them feel "
        "completely seen. Part 2 — What shifts: one insight that "
        "reframes how they see the situation; not a list, one clear "
        "shift. Part 3 — Where they go: what becomes possible after the "
        "shift; end with one specific question as the CTA. Short, "
        "direct, conversational — sound like a real person, not a brand."
    ),
    "check_script": (
        "Here is my post: [DRAFT]. Review it using the 3-part script: "
        "Part 1 — Where they are: does it name an exact situation, do "
        "they feel completely seen? Part 2 — What shifts: is there one "
        "clear insight or reframe, or does it just inform without "
        "shifting? Part 3 — Where they go: does the reader end up "
        "somewhere different from where they started, and is the CTA a "
        "clear next step? Tell me what is working and what to fix. Be "
        "direct."
    ),
}

# ---------------------------------------------------------------------------
# Full Visibility Era foundation — the remaining days beyond DAY 1/2/6/8/12
# plus the Messaging House / ICP from the Purely Personal content-foundation
# report (Back 2 business/content-foundation-themis.html). `foundations`
# merges all of these into voice_signature.
# ---------------------------------------------------------------------------

FOUNDATIONS = {
    "execution_rules": (
        "DAY 0 rules: Done beats perfect - always. Results come from "
        "posting, not planning. Ship the post, then improve the next one. "
        "A missed day breaks the streak - the system exists to make "
        "skipping harder than posting."
    ),
    "audience_mirror": (
        "DAY 7 - write for ONE person: a small-business founder or "
        "small-team owner (5-20 people) who has been burned before - a "
        "freelancer who vanished, an agency that overpromised, an invoice "
        "they could not explain. They feel cautious, tired of jargon, "
        "suspicious of AI hype. They want: something that actually gets "
        "delivered, in plain language, without chasing the vendor. They "
        "keep thinking 'maybe this won't work because I've already tried "
        "and got burned.' Advice they distrust: 'just post more', 'just "
        "use AI', 'just move to the cloud'."
    ),
    "messaging_house": (
        "UVP: Cloudless builds websites, hosting infrastructure and AI "
        "automations for small businesses that have been let down before "
        "- fixed scope, one point of contact from brief to go-live, "
        "technology that quietly works. Pillars: (1) People Before AI - "
        "understand the business first, add tech only where it removes "
        "friction; (2) Fixed Scope, No Surprises - written fixed price, "
        "no scope-creep invoices; (3) AI Is Not the Enemy - small concrete "
        "automations (booking bot, auto-reply, weekly report) that save "
        "hours without replacing people. Tagline: 'websites and AI that "
        "finally do what they promised.' POD: we serve the post-bad-vendor "
        "client and name what went wrong last time. POP: maintain visible "
        "technical credibility (portfolio, certifications, real numbers)."
    ),
    "hook_system": (
        "DAY 11 Hook Master. A hook must: stop the scroll completely, "
        "open a gap the reader needs closed, make them want the next "
        "line, and show a situation they instantly recognize. Killers: "
        "generic openers, vague questions, revealing the answer, no "
        "specific situation, cleverness over clarity. Rotate types: pain, "
        "specific-number, effort-vs-result, curiosity (open loop), "
        "contrast, callout, hard truth, competitor, confession, "
        "micro-story, pattern interrupt, insider, future-risk, receipt. "
        "Lens endings: lock the base hook, rotate the ending - "
        "cause/pattern for awareness, consequence/breakpoint for urgency, "
        "blunt/internal-issue for authority, question for engagement, "
        "emotional for relatability."
    ),
    "authority_formats": (
        "DAY 10 Authority Vault - rotate three formats: CLEAR TAKE "
        "(common situation > your take > simple explanation); WHAT "
        "ACTUALLY WORKS (what people usually do > what actually works > "
        "why); SIMPLE BREAKDOWN (problem > 1-2 steps > short explanation). "
        "Goal: be clear enough to be remembered, not smart enough to "
        "impress."
    ),
    "show_dont_tell": (
        "DAY 14 - never claim expertise ('10 years experience', 'hundreds "
        "of clients'). Show it: a specific moment, a real number, a client "
        "detail, a before/after. If a sentence could come from any "
        "competitor, replace it with a moment only we could describe."
    ),
    "content_that_leads": (
        "DAY 13 - a post moves the reader from their current state to a "
        "better one; by the end they should feel like they already moved. "
        "Script the journey: where they are > the shift > where they "
        "land. Information alone doesn't convert; movement does."
    ),
    "pre_publish_checklist": (
        "DAY 9 - before any post ships: (1) first line earns the second; "
        "one clear point only; sounds like a person not a template; "
        "would we stop scrolling for it. (2) short sentences; no "
        "smart-sounding words; post survives removing the first line; "
        "reads clean out loud. (3) exactly one CTA that matches the post "
        "and feels like a natural next step. Fix the one broken thing - "
        "never rewrite the whole post."
    ),
    "week4_video": (
        "Video (TikTok/reels/shorts): default format is B-roll + overlay "
        "text (DAY 17) - script runs as text over footage, no "
        "face-recording needed. When talking-head is required, prepare "
        "with the 2-Line Method and record ONE take (DAY 16) - friction "
        "before pressing record is perfectionism, not preparation. Batch "
        "method (DAY 20): one focused hour produces a week of video. Edit "
        "for clarity not perfection, under 30 minutes (DAY 19). Reuse the "
        "content bank - pillars, hooks, scripts - never start from "
        "scratch (DAY 18)."
    ),
    "content_bank": (
        "DAY 18 rule: the content bank already has everything - Week-2 "
        "pillars, Week-3 scripts, hooks, stories, frameworks. Every new "
        "piece starts by pulling an existing idea and re-cutting it into "
        "the target format; never invent a topic from zero."
    ),
}

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
    data = {'username': os.environ['SOCIAL_ADMIN_EMAIL'],
            'password': os.environ['SOCIAL_ADMIN_PASSWORD']}
    if secret:  # TOTP only when the account actually has it enabled
        key = base64.b32decode(secret)
        msg = struct.pack('>Q', int(time.time()) // 30)
        d = hmac.new(key, msg, hashlib.sha1).digest()
        o = d[-1] & 0x0F
        data['otp'] = str(
            (struct.unpack('>I', d[o:o+4])[0] & 0x7FFFFFFF) % 10**6
        ).zfill(6)
    async with httpx.AsyncClient() as c:
        r = await c.post('http://localhost:8000/api/v1/auth/login', data=data)
        r.raise_for_status()
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


def cmd_blueprint(apply: bool = False) -> None:
    sig = _api("GET", "/brand/voice").get("voice_signature", {}) or {}
    live = sig.get("post_blueprint", "")
    print("voice_signature.post_blueprint:")
    print(" ", live or "(not set — run 'creator_type.py blueprint apply')")
    if not apply:
        print("\nDAY 12 blueprint to apply:")
        print(" ", DAY12_BLUEPRINT)
        print(" ", DAY12_MISTAKES)
        return
    merged = {
        **sig,
        "post_blueprint": DAY12_BLUEPRINT,
        "blueprint_mistakes": DAY12_MISTAKES,
    }
    _api("PUT", "/brand/voice", {"voice_signature": merged})
    print("\nApplied post_blueprint + blueprint_mistakes.")
    if live and live != DAY12_BLUEPRINT:
        print("NOTE: replaced a previously set post_blueprint")


def cmd_foundations(apply: bool = False) -> None:
    sig = _api("GET", "/brand/voice").get("voice_signature", {}) or {}
    missing = [k for k in FOUNDATIONS if not sig.get(k)]
    drifted = [k for k in FOUNDATIONS if sig.get(k) and sig[k] != FOUNDATIONS[k]]
    print("FOUNDATIONS keys:", ", ".join(FOUNDATIONS))
    print(f"live: {len(FOUNDATIONS) - len(missing) - len(drifted)} exact, "
          f"{len(drifted)} drifted, {len(missing)} missing")
    if drifted:
        print("drifted:", ", ".join(drifted))
    if not apply:
        print("\nrun 'creator_type.py foundations apply' to write all keys")
        return
    _api("PUT", "/brand/voice", {"voice_signature": {**sig, **FOUNDATIONS}})
    print("applied.")


def cmd_day13(apply: bool = False) -> None:
    sig = _api("GET", "/brand/voice").get("voice_signature", {}) or {}
    live = sig.get("content_that_leads", "")
    print("voice_signature.content_that_leads:")
    print(" ", (live if isinstance(live, str) else json.dumps(live))[:300])
    print("\nvoice_signature.day13_formats keys:",
          ", ".join((sig.get("day13_formats") or {}).keys()) or "(not set)")
    if not apply:
        print("\nrun 'creator_type.py day13 apply' to write DAY 13 "
              "(content_that_leads + day13_formats + day13_prompts)")
        return
    merged = {
        **sig,
        "content_that_leads": DAY13_LEADS,
        "day13_formats": DAY13_FORMATS,
        "day13_prompts": DAY13_PROMPTS,
    }
    _api("PUT", "/brand/voice", {"voice_signature": merged})
    print("\nApplied content_that_leads + day13_formats + day13_prompts.")


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
    elif cmd == "blueprint":
        cmd_blueprint(apply=len(sys.argv) > 2 and sys.argv[2] == "apply")
    elif cmd == "foundations":
        cmd_foundations(apply=len(sys.argv) > 2 and sys.argv[2] == "apply")
    elif cmd == "day13":
        cmd_day13(apply=len(sys.argv) > 2 and sys.argv[2] == "apply")
    elif cmd == "verify":
        cmd_verify(sys.argv[2] if len(sys.argv) > 2 else "linkedin")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
