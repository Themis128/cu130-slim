"""Content sanitizer — post-text cleanup + quality gate before publish.

Fixes the three content bugs: Slack emoji-shortcodes leaking as raw text
(:robot_face: → 🤖), repeated connectives ("Furthermore," ×2), and
near-duplicate templated copy.
"""
import re

# Minimal shortcode→emoji map for the common Slack/GitHub names the DMR model emits.
EMOJI = {
    "robot_face": "🤖", "rocket": "🚀", "chart_with_upwards_trend": "📈",
    "fire": "🔥", "bulb": "💡", "hammer_and_wrench": "🛠️",
    "white_check_mark": "✅", "warning": "⚠️", "gear": "⚙️",
    "cloud": "☁️", "lock": "🔒", "money_with_wings": "💸",
    "tada": "🎉", "eyes": "👀", "star": "⭐", "zap": "⚡",
    "sparkles": "✨", "point_right": "👉", "link": "🔗", "books": "📚",
    "globe_with_meridians": "🌐", "speech_balloon": "💬", "chart": "📊",
    "shield": "🛡️", "hourglass": "⏳", "calendar": "📅", "pushpin": "📌",
    "wrench": "🔧", "tools": "🛠️", "trophy": "🏆", "gift": "🎁",
    "handshake": "🤝", "bulb2": "💡", "earth_africa": "🌍", "earth_americas": "🌎",
    "earth_asia": "🌏", "heavy_check_mark": "✔️", "x": "❌", "package": "📦",
    "computer": "💻", "iphone": "📱", "email": "📧", "envelope": "✉️",
    "phone": "📞", "loudspeaker": "📢", "mega": "📣", "dart": "🎯",
    "money_bag": "💰", "credit_card": "💳", "bar_chart": "📊",
    "thumbsup": "👍", "thumbs_up": "👍", "clap": "👏", "muscle": "💪",
    "wave": "👋", "raised_hands": "🙌", "pray": "🙏",
}

_SHORTCODE_RE = re.compile(r":([a-z0-9_+\-]{2,40}):")
_CONNECTIVE_RE = re.compile(r"\b(Furthermore|Moreover|Additionally|Also|In addition),?\s+", re.I)


def _demojize(text: str) -> str:
    """Replace :name: shortcodes with real emoji; drop unresolvable codes."""
    def repl(m):
        name = m.group(1)
        return EMOJI.get(name, "")
    # collapse runs created by dropped codes (".... :x: ..." → ".... ...")
    out = _SHORTCODE_RE.sub(repl, text)
    return re.sub(r" {2,}", " ", out)


def _dedupe_connectives(text: str) -> str:
    """Drop a connective that repeats the one before it sentence-to-sentence
    ('Furthermore, x. Furthermore, y.' → 'Furthermore, x. y.')."""
    out = []
    prev_conn = ""
    for sent in re.split(r"(?<=[.!?])\s+", text):
        m = _CONNECTIVE_RE.match(sent)
        if m:
            conn = m.group(1).lower()
            if conn == prev_conn:
                sent = sent[m.end():]
            else:
                prev_conn = conn
        else:
            prev_conn = ""
        out.append(sent)
    return " ".join(out)


def sanitize_post_text(text: str) -> str:
    """The publish-path sanitizer — emoji + connective cleanup."""
    if not text:
        return text
    return _dedupe_connectives(_demojize(text)).strip()


def content_quality_gate(text: str, recent_texts: list[str],
                         sim_threshold: float = 0.85) -> tuple[bool, str]:
    """Reject near-duplicates vs recent posts. Returns (ok, reason)."""
    def shingles(s: str) -> set:
        w = re.findall(r"\w+", s.lower())
        return {" ".join(w[i:i+3]) for i in range(max(0, len(w)-2))}
    if not text.strip():
        return False, "empty"
    mine = shingles(text)
    if not mine:
        return True, "ok"
    for r in recent_texts:
        theirs = shingles(r)
        if not theirs:
            continue
        sim = len(mine & theirs) / len(mine | theirs)
        if sim >= sim_threshold:
            return False, f"near-duplicate (sim={sim:.2f})"
    return True, "ok"


# --- generation-side quality gates (2026 AI-slop research) ---

# LLM-favorite filler that flags synthetic copy — the "slop" markers.
SLOP_WORDS = {
    "delve", "delves", "delving", "showcase", "showcasing", "underscore",
    "crucial", "leverage", "leveraging", "synergy", "paradigm",
    "unlock", "unlocking", "elevate", "empower", "supercharge",
    "cutting-edge", "game-changer", "revolutionize", "navigate",
    "landscape", "tapestry", "realm", "underscores",
}
BANNED_OPENERS = ("in today's", "in the ever-evolving", "as we navigate",
                  "welcome to", "are you ready to", "in this digital age")
MAX_SENTENCE_WORDS = 30


def slop_scan(text: str) -> list[str]:
    """Return the LLM-slop markers present — the generation should revise if
    these exceed a few per post."""
    words = set(re.findall(r"[a-z\'-]+", text.lower()))
    hits = sorted(words & SLOP_WORDS)
    for op in BANNED_OPENERS:
        if text.lower().startswith(op):
            hits.append(f"opener:{op}")
    return hits


def voice_check(text: str) -> tuple[bool, str]:
    """Voice-constraint check — slop markers + run-on sentences. Returns
    (passes, reason)."""
    hits = slop_scan(text)
    if len(hits) >= 4:
        return False, f"slop: {', '.join(hits[:4])}"
    for s in re.split(r"(?<=[.!?])\s+", text):
        if len(re.findall(r"\w+", s)) > MAX_SENTENCE_WORDS:
            return False, f"run-on ({len(re.findall(r'\w+', s))}w)"
    return True, "ok"
