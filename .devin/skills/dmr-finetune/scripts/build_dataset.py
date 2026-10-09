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

# --- Seed pairs: terse Cloudless-domain topic -> realistic dense prompt ------
# Target style: photographic, physically plausible, concrete props/settings.
SEED_PAIRS = [
    # (terse, expanded)
    ("cloud bill sticker shock",
     "Photograph of a small business owner at a kitchen table holding a long printed invoice with a shocked expression, laptop open showing a confusing pricing dashboard, warm morning light through a window, shallow depth of field, realistic documentary style"),
    ("self-hosted home lab",
     "Photo of a compact home server rack on a wooden shelf in a home office, small black mini-PCs and a network switch with tidy ethernet cables, soft monitor glow from a desk nearby, realistic indoor photography, natural lighting"),
    ("small team collaboration",
     "Realistic photo of three coworkers around a wooden office desk reviewing a laptop screen together, coffee mugs and notebooks scattered, soft afternoon window light, candid documentary style, shallow depth of field"),
    ("raspberry pi cluster build",
     "Close-up photo of a stack of Raspberry Pi boards mounted in a clear acrylic case with visible ribbon cables and status LEDs lit, on a cluttered maker workbench with tools, warm desk lamp light, macro-style realistic photo"),
    ("migrating off expensive cloud",
     "Photo of a developer's desk with two monitors showing a terminal and a migration checklist, a hand-written sticky note on the bezel, coffee cup, evening warm lamp light, realistic candid shot, shallow depth of field"),
    ("fixed price hosting relief",
     "Realistic photo of a relaxed freelancer leaning back in a chair at a tidy desk, laptop closed, a calm satisfied expression, bright daylight from a large window, plants in background, candid lifestyle photography"),
    ("vendor lock-in frustration",
     "Photo of a frustrated developer with hands on head in front of a monitor showing an error dialog, dim blue-lit office at night, realistic candid workplace shot, shallow depth of field"),
    ("deploy with git push",
     "Photo of fingers on a laptop keyboard mid keypress, screen showing a terminal with a green success checkmark, clean modern desk, natural window light, realistic close-up photography"),
    ("monitoring dashboard green",
     "Photo of a wall-mounted monitor showing a status dashboard of green uptime bars, dim office with reflections on the screen, realistic photography, slightly elevated angle"),
    ("backup drives redundancy",
     "Photo of two external hard drives on a wooden desk next to a notebook labeled with checkmarks, warm afternoon light, realistic still-life photography, shallow depth of field"),
    ("email on own domain",
     "Photo of a tablet on a cafe table showing an email inbox interface, a ceramic espresso cup beside it, blurred cafe interior background, morning light, realistic lifestyle photo"),
    ("home office setup",
     "Photo of a tidy home office corner with a laptop on a wooden desk, small bookshelf, warm lamp light in early evening, realistic interior photography, cozy atmosphere"),
    ("developer debugging at night",
     "Realistic photo of a developer silhouetted against a monitor full of code, dark room lit by the screen, keyboard and energy drink can on desk, moody blue ambient light, candid shot"),
    ("automation pipeline working",
     "Photo of a laptop on a desk showing a flowchart-like interface with connected nodes, sticky notes on the wall behind, daylight, realistic office photography"),
    ("weekend side project",
     "Photo of a person sketching a website wireframe on paper at a kitchen table on a weekend morning, croissant and coffee nearby, bright natural light, candid realistic style"),
    ("cost comparison spreadsheet",
     "Photo of a tablet and printed paper side by side on a desk, comparing two price columns, a pen pointing at the lower number, office daylight, realistic top-down shot"),
    ("container orchestration",
     "Photo of a screen showing rows of small status tiles like shipping containers in a grid, a hand adjusting a knob on a desk device, soft bokeh office background, realistic photo"),
    ("server room mini rack",
     "Photo of a small 10-inch rack cabinet under a desk with glowing switch LEDs and neatly tied cables, dim room, cool blue accent light from the LEDs, realistic photo"),
    ("video call with client",
     "Photo of a laptop on a home desk mid video-call showing a smiling client on screen, notebook and pen beside it, bright daytime light, realistic candid shot"),
    ("launch day excitement",
     "Photo of two people fist-bumping over a desk where a laptop shows a live website, celebratory energy, warm afternoon light, realistic candid office photo"),
    # Anti-cliché pairs — ground the abstract prompts that produced
    # glow-mush output in production (e.g. "glowing feed" -> blown-out blur)
    ("social media feed glow",
     "Photo of a hand holding a smartphone showing a social feed interface of colorful photo cards, cozy cafe background with warm pendant lights bokeh, shallow depth of field, realistic photo"),
    ("ai brain magic",
     "Photo of a developer's monitor showing a neural-network diagram with connected nodes, hands typing on the keyboard below, warm desk light, realistic candid shot — no fantasy imagery"),
    ("digital transformation",
     "Photo of an office wall whiteboard covered with process flow diagrams and sticky notes, a person pointing at one node, bright daylight, realistic documentary style"),
    ("data flow visualization",
     "Photo of a large monitor showing a line graph dashboard, dim office, a mug and notebook on the desk in front, cool screen light, realistic photo"),
    ("secure infrastructure",
     "Photo of a server rack cabinet with a closed glass door and green status LEDs, tidy cable runs visible, dim utility room, realistic photo"),
    ("cloud migration plan",
     "Photo of printed architecture diagrams spread on a conference table with a hand pointing at an arrow between boxes, overhead daylight, realistic top-down shot"),
    ("uptime monitoring",
     "Photo of a wall screen showing an uptime dashboard with green bars and one red alert tile, a person standing below looking up, cool office lighting, realistic candid shot"),
    ("password manager vault",
     "Photo of a hand holding a smartphone showing a lock screen icon over a clean app interface, on a dark desk with keyboard edge visible, warm evening light, realistic close-up"),
    ("api integration",
     "Photo of two monitors side by side, one showing code with JSON and the other a dashboard, a hand reaching between them, desk daylight, realistic photo"),
    ("team standup meeting",
     "Photo of four people standing in a loose circle around a kanban board with sticky notes, one writing on it, bright morning office light, realistic candid shot"),
    ("quiet focus work",
     "Photo of a person wearing headphones typing on a laptop in a library-like quiet room, warm lamp pools of light, shallow depth of field, realistic photo"),
    ("shipping a release",
     "Photo of a developer pressing the enter key on a laptop whose screen shows a green progress bar completing, celebratory posture, evening warm light, realistic candid shot"),
    ("customer support reply",
     "Photo of a support agent at a desk typing a reply on a chat window, headset around neck, calm friendly expression, office daylight, realistic candid photo"),
    ("open source contribution",
     "Photo of a laptop screen showing a pull-request interface with a green merged badge, a mug and a small plant beside it, morning light, realistic close-up"),
    ("hardware tinkering",
     "Photo of hands holding a screwdriver over an open mini-PC exposing a circuit board and RAM slots, workbench with parts scattered, warm workshop light, realistic macro-style photo"),
    ("reading server logs",
     "Photo of a monitor filled with scrolling monospace log lines, a pair of glasses resting on the desk in front, dim room, screen glow, realistic photo"),
    ("retro computing nostalgia",
     "Photo of an old beige CRT monitor and chunky keyboard on a wooden desk beside a modern laptop, warm nostalgic afternoon light, realistic still-life photo"),
    ("brainstorming session",
     "Photo of sticky notes in many colors arranged on a glass wall, two people discussing with a marker in hand, bright office light, realistic candid shot"),
    ("deploy friday meme mood",
     "Photo of a developer with one finger hovering nervously over the keyboard, monitor showing a deploy button, colleagues watching over the shoulder, office humor candid, realistic photo"),
    ("green energy datacenter",
     "Photo of a small server shelf with a potted plant on top and a window view of trees behind, morning light, blending tech and nature, realistic photo"),
    # Abstract business concepts — the mappings production actually sends.
    # Each gets a DISTINCT scene so the model learns topic->scene, not
    # a repeated scaffold.
    ("surprise cloud invoice",
     "Photograph of a café owner turning a long printed receipt over in disbelief at the counter, card terminal and pastry display beside, warm afternoon light, realistic candid shot"),
    ("predictable monthly pricing",
     "Photo of a tidy desk with a wall calendar, a laptop showing a simple plan page, and a hand writing a single recurring figure in a notebook, calm daylight, realistic office photo"),
    ("cancel anytime no lock-in",
     "Photo of a person sliding a laptop shut and smiling, jacket over shoulder ready to leave, bright lobby light behind, candid realistic lifestyle shot"),
    ("migration weekend plan",
     "Photo of a whiteboard timeline with arrows and checkboxes, two mugs and a weekend bag under the desk, a hand ticking the last box, evening office light, realistic documentary photo"),
    ("downtime outage alert",
     "Photo of a phone on a nightstand lighting up with a red alert screen at night, a pair of glasses and a watch beside it, dark room lit only by the phone, realistic close-up"),
    ("analytics growth curve",
     "Photo of a printed line chart trending upward on a desk, a hand holding a pen circling the peak, laptop edge visible, morning office light, realistic top-down shot"),
    ("customer churn worry",
     "Photo of a founder staring at an empty chair across a cafe table, two coffees one untouched, thoughtful expression, soft window light, realistic candid photo"),
    ("subscription fatigue",
     "Photo of a wallet with many small paper receipts fanned out on a desk, a tired hand resting on the mouse, warm lamp light, realistic still-life candid"),
    ("launch week nerves",
     "Photo of a team member refreshing a laptop screen repeatedly while colleagues watch, tension and coffee cups on the desk, early morning office light, realistic candid shot"),
    ("flat rate vs usage billing",
     "Photo of two paper lists side by side on a table, one short with a single total and one long with itemized lines, a finger pointing at the short one, daylight, realistic top-down shot"),
    ("self hosted email inbox",
     "Photo of an older laptop on a kitchen table showing a mail inbox, breakfast plate pushed aside, morning light through blinds, realistic documentary style"),
    ("on call rotation",
     "Photo of a shared desk calendar with color-coded name tags and a phone charging on top, dim hallway light, quiet office after hours, realistic photo"),
    ("data export freedom",
     "Photo of a hand plugging a USB drive into a laptop showing a file-download progress bar, desk plant and coffee nearby, daylight, realistic candid shot"),
    ("monorepo cleanup day",
     "Photo of a monitor showing a long file tree being tidied, sticky note tabs along the screen edge, a satisfied posture in the chair, afternoon light, realistic photo"),
    ("incident postmortem notes",
     "Photo of a notebook with a hand-written timeline and arrows, a laptop showing a chat thread beside it, calm focused desk scene, cool daylight, realistic top-down photo"),
]

# --- Augmentation: subjects x settings x lighting x camera -------------------
SUBJECTS = [
    ("a developer typing on a mechanical keyboard", "mechanical keyboard close-up"),
    ("a small business owner reviewing invoices on a tablet", "invoice review"),
    ("a freelancer sketching app wireframes on paper", "wireframe sketching"),
    ("a sysadmin checking a home server status panel", "server check"),
    ("a designer arranging printed logo drafts on a desk", "logo drafts"),
    ("a remote worker in a video meeting with headphones", "remote meeting"),
    ("a founder whiteboard-sketching a system diagram", "architecture whiteboard"),
    ("a developer pointing at a code diff on a monitor", "code review"),
    ("an engineer mounting a device inside a rack shelf", "rack install"),
    ("a person scrolling a social feed on a smartphone", "scrolling feed"),
    ("a coffee cup steaming next to a laptop running a build", "morning build"),
    ("hands plugging an ethernet cable into a small switch", "network cabling"),
    ("a notebook open with a hand-drawn funnel diagram", "funnel planning"),
    ("a monitor showing a CI pipeline of green checkmarks", "green pipeline"),
    ("a shelf with labeled backup drives and a NAS box", "backup shelf"),
]
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

def main() -> None:
    rng = random.Random(42)
    img_rows, vid_rows = [], []

    # Real production hooks → both variants (actual data only)
    for hook, img, vid in real_pairs(rng):
        img_rows.append(row(SYSTEM_IMG, hook, img))
        vid_rows.append(row(SYSTEM_VID, hook, vid))

    rng.shuffle(img_rows)
    rng.shuffle(vid_rows)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "media_prompts.jsonl").write_text(
        "\n".join(json.dumps(r) for r in img_rows) + "\n")
    (OUT / "media_prompts_video.jsonl").write_text(
        "\n".join(json.dumps(r) for r in vid_rows) + "\n")
    print(f"wrote {len(img_rows)} image rows, {len(vid_rows)} video rows to {OUT}")

if __name__ == "__main__":
    sys.exit(main())
