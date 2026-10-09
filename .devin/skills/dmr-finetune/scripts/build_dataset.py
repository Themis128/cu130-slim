#!/usr/bin/env python3
"""Build the media-prompt fine-tune dataset for DMR models.

Produces JSONL chat rows matching the real expand_visual_prompt call in
media_ai.py — same system prompt, terse user input, dense photorealistic
assistant output. Focus: REAL-LIFE accuracy — physically plausible scenes,
real camera/lighting language, no fantasy glow or impossible objects.

Output: data/media_prompts.jsonl (+ a -video.jsonl variant).
"""
import json
import random
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data"

SYSTEM_IMG = (
    "You expand terse visual descriptions into detailed generation prompts "
    "for a diffusion model that renders a image. Rules: one dense sentence "
    "or two; concrete subject, setting, lighting, color, composition; "
    "no text/letters/watermarks; no meta-commentary; output the prompt only."
)
SYSTEM_VID = (
    "You expand terse visual descriptions into detailed generation prompts "
    "for a diffusion model that renders a short video clip. Rules: one dense "
    "sentence or two; concrete subject, setting, lighting, color, "
    "composition, and camera motion; no text/letters/watermarks; no "
    "meta-commentary; output the prompt only."
)

SETTINGS = [
    "on a wooden home-office desk",
    "at a bright kitchen table",
    "in a small office with white walls",
    "at a cafe table with a blurred interior behind",
    "in a dim room lit by the screen glow",
    "on a cluttered maker workbench",
    "at a standing desk near a window",
    "in a cozy corner with a bookshelf",
]
LIGHTING = [
    "soft natural window light",
    "warm desk-lamp light in the evening",
    "golden-hour sunlight through blinds",
    "cool blue ambient light from monitors",
    "bright overcast daylight",
    "warm tungsten ceiling light",
    "diffused morning light",
    "late-afternoon sun casting long shadows",
    "a single pool of lamplight in a dark room",
]
CAMERA = [
    "shallow depth of field, 50mm lens look",
    "slightly elevated angle, realistic photo",
    "close-up macro-style detail",
    "wide candid shot, documentary style",
    "eye-level natural framing",
    "over-the-shoulder perspective",
    "top-down flat composition",
]
# Optional concrete props that broaden the output vocabulary without
# changing the sentence shape.
DETAILS = [
    "a ceramic coffee mug nearby",
    "papers scattered beside it",
    "a small plant on the windowsill",
    "sticky notes along the monitor edge",
    "a half-open notebook next to it",
    "headphones resting on the desk",
    "a pen lying across a printed page",
    "cables neatly tied behind",
    "a water bottle and phone beside it",
    "reading glasses on a stack of papers",
    "",
    "",
    "",
]
MOTIONS = [  # video only
    "slow camera push-in",
    "gentle handheld sway",
    "subtle dolly slide to the right",
    "static tripod shot with natural subject motion",
    "slow tilt up",
]

def expand(subject_full: str, setting: str, lighting: str, camera: str,
           rng, detail: str = "") -> str:
    d = f", {detail}" if detail else ""
    return (
        f"Realistic photo of {subject_full} {setting}{d}, {lighting}, "
        f"{camera}, candid documentary style"
    )

def expand_video(subject_full, setting, lighting, camera, motion, rng,
                 detail: str = "") -> str:
    d = f", {detail}" if detail else ""
    return (
        f"Realistic video clip of {subject_full} {setting}{d}, {lighting}, "
        f"{camera}, {motion}, natural motion"
    )

# --- Real production inputs -------------------------------------------------
# Hooks from actually-published posts (data/real_posts.json, dumped from the
# posts table) — the distribution expand_visual_prompt really sees. Each hook
# is mapped to a grounded scene by keyword; the scaffold stays fixed so the
# output distribution stays peaked.
REAL_RULES = [  # (keywords, [(subject, setting), ...]) — several scene
    # variants per theme so no single target phrase dominates training
    (("shop", "store", "checkout", "sell online", "e-commerce"), [
        ("an online store page shown on a laptop next to a smartphone with a product grid",
         "on a tidy desk in a small shop office"),
        ("a shop owner holding a tablet showing an order list behind a counter",
         "in a small retail shop"),
        ("a laptop open to a checkout page beside a cardboard parcel and tape",
         "on a packing table in a back room"),
    ]),
    (("checklist", "audit", "guide", "steps", "list"), [
        ("a printed checklist on a clipboard with boxes ticked in pen",
         "on a wooden desk next to a laptop and coffee"),
        ("a hand ticking items on a printed worksheet beside a laptop",
         "at a bright kitchen table"),
        ("a notebook open to a hand-written numbered list with a pen resting on it",
         "on an office desk near a window"),
    ]),
    (("cost", "bill", "invoice", "€", "$0", "price", "budget", "save", "free tier", "free-tier"), [
        ("a printed invoice and a laptop showing a cost dashboard with a single low figure",
         "at a kitchen table with morning light"),
        ("a small business owner holding a short printed receipt next to a card terminal",
         "behind a shop counter"),
        ("a notebook with a single monthly figure written large beside a calculator",
         "on a tidy desk"),
    ]),
    (("hosting", "cloud", "serverless", "server", "cloudflare", "infrastructure", "deploy"), [
        ("a compact home server stack of mini-PCs and a network switch with green status LEDs",
         "on a wooden shelf in a home office"),
        ("a small rack cabinet under a desk with tidy ethernet cables and blinking LEDs",
         "in a dim utility corner"),
        ("a mini-PC and a small switch mounted on a wall shelf with labeled cables",
         "in a tidy home workspace"),
    ]),
    (("automat", "pipeline", "workflow", "publishes", "runs itself", "while i slept"), [
        ("a laptop screen showing an automation flowchart of connected nodes",
         "on a desk in a dim room lit by the screen"),
        ("a monitor showing a sequence of scheduled tasks with green checkmarks",
         "on a night desk beside a phone charging"),
        ("hands typing beside a screen showing a visual pipeline editor",
         "on a cluttered workbench"),
    ]),
    (("post", "social", "media", "publish", "content", "write"), [
        ("a smartphone held in a hand showing a social feed of photo cards",
         "with a blurred cafe interior behind"),
        ("a laptop showing a post editor with a photo attached and a publish button",
         "on a home-office desk"),
        ("a person reviewing a social feed on a phone resting on a notebook",
         "at a kitchen table"),
    ]),
    (("monitor", "uptime", "status", "dashboard", "green", "pod"), [
        ("a wall-mounted monitor showing a status dashboard of green uptime bars",
         "in a dim office"),
        ("a laptop screen filled with green status tiles and a small terminal window",
         "on a desk with a mechanical keyboard"),
        ("a monitor showing line graphs trending flat and green",
         "in a dark room with screen glow"),
    ]),
    (("pi ", "raspberry", "cluster", "homelab", "self-host", "build log"), [
        ("a stack of single-board computers in a clear case with visible cables and status LEDs",
         "on a cluttered maker workbench"),
        ("a small tower of mini computers connected by short network cables",
         "on a wooden shelf beside tools"),
        ("hands plugging an ethernet cable into a stack of small circuit boards",
         "over a workbench with a soldering iron nearby"),
    ]),
    (("website", "fast", "speed", "page"), [
        ("a laptop showing a fast-loading business website homepage",
         "on a bright office desk near a window"),
        ("a designer pointing at a website layout on a large monitor",
         "in a small studio"),
        ("a smartphone and laptop side by side showing the same clean website",
         "on a cafe table"),
    ]),
    (("team", "startup", "small business", "founder"), [
        ("three coworkers reviewing a laptop screen together",
         "around a wooden office desk"),
        ("two founders looking at a whiteboard with a simple flow diagram",
         "in a small meeting room"),
        ("a small team gathered around a monitor discussing a chart",
         "in a bright shared office"),
    ]),
    (("video", "image", "visual", "generated"), [
        ("a monitor showing a grid of generated image thumbnails",
         "on a creator's desk in a warm-lit room"),
        ("a laptop rendering a photo-realistic image preview",
         "on a desk with a graphics tablet beside it"),
    ]),
    (("email", "inbox", "mail"), [
        ("a tablet showing an email inbox interface next to a ceramic espresso cup",
         "on a cafe table"),
        ("a laptop open to a mail client with a short message being typed",
         "on a kitchen table in the morning"),
    ]),
    (("night", "23:", "sleep", "morning commit", "coffee"), [
        ("a dark desk with a terminal window glowing on a monitor and a coffee cup beside the keyboard",
         "in a room lit only by the screen"),
        ("an empty chair in front of a monitor showing a finished build log",
         "in a dark home office"),
    ]),
]
REAL_FALLBACK = (
    "a laptop open on a tidy desk next to a notebook and a coffee cup",
    "in a bright home office",
)


def _hook(text: str) -> str:
    import re
    # Keep the first paragraph — production sends the caption lead, not the
    # hashtag/CTA tail. Strip emoji, hashtags, URLs, boilerplate.
    first = text.split("\n\n")[0]
    line = next((l.strip() for l in first.split("\n") if l.strip()), "")
    line = re.sub(r"https?://\S+|#\w+", " ", line)
    line = re.sub(r"[^\w\s€$%&'’\-–—.?!,:;()/]", " ", line)  # emoji/symbols
    return " ".join(line.split())[:110]


_BAD_TARGET = ("marketing image", "illustration", "infographic", "flat design",
               "logo concept", "gradient", "no text")


def _real_target(prompts) -> str:
    """Pick the post's real stored media prompt if it's photorealistic."""
    for p in prompts or []:
        if not p:
            continue
        low = p.lower()
        if any(b in low for b in _BAD_TARGET):
            continue
        if "photo" in low or "realistic" in low or len(p) > 110:
            return p.strip().rstrip(".")
    return ""


def _clean_input(text: str, limit: int) -> str:
    import re
    text = re.sub(r"https?://\S+|#\w+", " ", text)
    text = re.sub(r"[^\w\s€$%&'’\-–—.?!,:;()/]", " ", text)
    return " ".join(text.split())[:limit]


def _scene_for(low: str, rng) -> str:
    subj, setting = REAL_FALLBACK
    for keys, variants in REAL_RULES:
        if any(k in low for k in keys):
            subj, setting = rng.choice(variants)
            break
    return expand(subj, setting, rng.choice(LIGHTING), rng.choice(CAMERA),
                  rng, rng.choice(DETAILS))


# Meta/style fragments stripped from real prompts when building targets —
# they describe format, not scene, and must not leak into the expanded text.
_META_RE = __import__("re").compile(
    r"(?i)\b(marketing image|illustration|infographic|icon|logo|concept|"
    r"aesthetic|minimalist|minimal|abstract|vertical|futuristic|sleek|"
    r"clean|modern|flat design|gradient|professional|stylized|neon|"
    r"photorealistic|realistic|photo|image|no text|no letters|no watermark|"
    r"brand|tech style|design|banner|cover)\b|#[0-9a-fA-F]{3,8}")


def _normalize_scene(p: str) -> str:
    """Reduce a real generation prompt to its scene content."""
    import re
    s = p.strip().rstrip(".")
    s = re.sub(r"(?i)^\s*(marketing image for|a clean infographic|"
               r"minimalist flat illustration of|realistic photo of|photo of)\s*:?",
               "", s).strip()
    s = _META_RE.sub("", s)
    s = re.sub(r"\s*,\s*,+", ",", s)
    s = re.sub(r"[\s,]+$", "", s)
    s = re.sub(r"\s{2,}", " ", s).strip(" ,.-")
    s = re.sub(r"(?i)^(of|for|with|a|an|the)\s+", "", s)
    return s[0].lower() + s[1:] if s else ""


# Scene fragments that aren't a photographable subject — prompts containing
# these get mapped onto a concrete scene instead of paraphrased.
_NONSCENE = ("title", "subtitle", ":", "#", "+", "—", "interface", "pattern",
             "poster", "banner", "logo", "tech", "no people", "no logos",
             "style", "palette", "background ,", "  ", "text", "cloudless.gr")


def _is_scene(scene: str) -> bool:
    if len(scene) <= 15:
        return False
    low = scene.lower()
    return not any(b in low for b in _NONSCENE)


def _expand_scene(scene: str, rng) -> str:
    """Rewrite a scene into the dense scaffold — the target the model learns."""
    return expand(scene, rng.choice(SETTINGS), rng.choice(LIGHTING),
                  rng.choice(CAMERA), rng, rng.choice(DETAILS))


def real_pairs(rng) -> list:
    """Inputs = real prompts actually sent to the image pipeline (media_assets).

    Targets = the SAME scene rewritten into the dense scaffold — a paraphrase
    task that teaches 'expand what was asked', not topic->scene guessing.
    """
    praw = OUT / "real_prompts.json"
    if not praw.exists():
        return []
    pairs, used = [], set()
    for e in json.loads(praw.read_text()):
        inp = _clean_input(e.get("prompt") or "", 110)
        scene = _normalize_scene(e.get("prompt") or "")
        if len(inp) <= 20:
            continue
        for _ in range(20):
            # Paraphrase photographic prompts; map abstract/text-heavy ones
            # onto a concrete real scene (the point of the fine-tune).
            t = _expand_scene(scene, rng) if _is_scene(scene) \
                else _scene_for(inp.lower(), rng)
            if t not in used:
                break
        used.add(t)
        vid = t.replace("Realistic photo of", "Realistic video clip of") + \
            f", {rng.choice(MOTIONS)}"
        pairs.append((inp, t, vid))
    return pairs


def row(system: str, user: str, assistant: str) -> dict:
    return {"messages": [
        {"role": "system", "content": system},
        # call_dmr_chat appends " /no_think" to user turns for qwen3 models —
        # train on the exact production input, not a cleaned-up version.
        {"role": "user", "content": user + " /no_think"},
        {"role": "assistant", "content": assistant},
    ]}

import re as _re

_SECRET_RE = _re.compile(
    r"(eyJ[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9]{20,}|xox[baprs]-|"
    r"AKIA[0-9A-Z]{16}|-----BEGIN|Bearer\s+\S{20,})", _re.I)
_PROTOCOL = ('<|im_', '<tool_call', '<think')


def validate(rows: list, name: str) -> None:
    """Fail loudly if the dataset violates the rules that broke past runs."""
    users = [m["content"] for r in rows for m in r["messages"] if m["role"] == "user"]
    tgts = [m["content"] for r in rows for m in r["messages"] if m["role"] == "assistant"]
    assert rows, f"{name}: empty dataset"
    assert all(u.strip().endswith("/no_think") for u in users),         f"{name}: user turns missing the production /no_think suffix"
    assert all(20 <= len(t) <= 600 for t in tgts),         f"{name}: target length out of bounds"
    joined = "\n".join(tgts + users)
    for frag in _PROTOCOL:
        assert frag not in joined, f"{name}: protocol fragment {frag!r} leaked"
    assert not _SECRET_RE.search(joined), f"{name}: secret-like string found"
    dup = 1 - len(set(tgts)) / len(tgts)
    assert dup < 0.10, f"{name}: {dup:.0%} duplicate targets (memorization risk)"
    print(f"{name}: {len(rows)} rows, {dup:.0%} dup targets, all checks pass")


def main() -> None:
    rng = random.Random(42)
    img_rows, vid_rows = [], []

    # Real production hooks → both variants (actual data only)
    for hook, img, vid in real_pairs(rng):
        img_rows.append(row(SYSTEM_IMG, hook, img))
        vid_rows.append(row(SYSTEM_VID, hook, vid))

    rng.shuffle(img_rows)
    rng.shuffle(vid_rows)
    validate(img_rows, "media_prompts.jsonl")
    validate(vid_rows, "media_prompts_video.jsonl")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "media_prompts.jsonl").write_text(
        "\n".join(json.dumps(r) for r in img_rows) + "\n")
    (OUT / "media_prompts_video.jsonl").write_text(
        "\n".join(json.dumps(r) for r in vid_rows) + "\n")
    print(f"wrote {len(img_rows)} image rows, {len(vid_rows)} video rows to {OUT}")

if __name__ == "__main__":
    sys.exit(main())
