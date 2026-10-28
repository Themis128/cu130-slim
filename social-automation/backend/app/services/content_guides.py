"""Shared content-generation constants — platform writing guides, hashtag
rules, and the structured-output schema used by ``/api/v1/ai/generate-content``
and the autonomous executors (brand agent, monetization executor).

Single source of truth so a platform-algorithm change (e.g. Instagram's 5-tag
cap) lands in one place for every generation path.
"""

from __future__ import annotations

PLATFORM_GUIDES: dict[str, str] = {
    "linkedin": (
        "Professional, thought-leadership style for LinkedIn. 3000 char limit. "
        "SEO: place the primary keyword in the first 140 characters (the mobile preview cutoff) — "
        "this is the hook that earns the 'see more' click. Use 2-3 semantic keyword variations "
        "throughout the body so LinkedIn's search engine can index the post. "
        "Structure for dwell time: hook → value proposition → substantive content (story, framework, "
        "or data) → closing question that invites a reply. Use line breaks for readability. "
        "Make the post save-worthy: include a checklist, framework, or data point someone would "
        "reference later. 3-5 hashtags only — more than 5 triggers spam-like signals. "
        "Mix niche (10K-500K posts), mid-tier, and one branded tag. Hashtags must be semantically "
        "relevant to the post text. Plain everyday English."
    ),
    "twitter": (
        "Concise, conversational for X/Twitter. 280 char limit. Thread-friendly. "
        "1-2 hashtags max — more dilutes the message. Place the keyword early. "
        "Plain everyday English."
    ),
    "instagram": (
        "Visual-first, engaging for Instagram. 2200 char limit. "
        "HARD CAP: maximum 5 hashtags per post (Instagram enforced this in December 2025 — "
        "extra tags are ignored and 20+ generic tags trigger distribution suppression). "
        "Use 3-5 hyper-relevant tags chosen per post, not recycled from a master list. "
        "Mix niche community, branded, and topic tags. Hashtags are classification signals "
        "for the algorithm, not reach drivers — caption keywords and on-screen text matter more. "
        "Use emojis. Plain everyday English."
    ),
    "facebook": ("Community-focused, conversational for Facebook. No strict limit. 1-3 hashtags. Plain everyday English."),
    "threads": (
        "Casual, conversational, text-first for Threads. 500 char limit. "
        "Minimal hashtags (0-2). Threads rewards authentic, human-sounding posts "
        "— write like a person, not a brand press release. Lead with a hook or "
        "opinion in the first line. Follow the brand voice signature and tone "
        "dimensions from the BRAND CONTEXT below. Plain everyday English."
    ),
    "tiktok": (
        "Short-form video/photo caption for TikTok. 2200 char limit. "
        "SEO: place the primary keyword in the first 150 characters — TikTok Search "
        "weights the opening caption text highest. Write 150-300 character captions for "
        "maximum indexable keyword surface area. Include long-tail keyword variations. "
        "3-5 targeted hashtags using a 3-tier mix: niche, mid-tier, and broad. "
        "More than 5 dilutes the topic signal and reduces FYP distribution. "
        "Hook in the first line. Use emojis. Plain everyday English."
    ),
    "telegram": (
        "Broadcast post for a Telegram channel — treated like a micro-landing page. "
        "Hard cap: 1024 characters (sent as a photo caption via sendPhoto). "
        "First line is the hook — under 90 characters, benefit-forward, names a "
        "situation the reader recognizes. Then short scannable lines or a numbered "
        "mini-framework — one job-to-be-done per post, never a content dump. "
        "Close with ONE call-to-action only (e.g. 'react with 🔥', 'get the full "
        "checklist below'). Zero or one hashtag at most — Telegram has no hashtag "
        "ranking; a series tag like [Playbook], [Build Log] or [Deal] in the first "
        "line works better for recurring formats. Emojis welcome, sparingly. "
        "Plain everyday English — write like a person, not a broadcast ad."
    ),
    "email": (
        "Marketing/survey email for the Cloudless email-campaigns system — the "
        "reader opted in or is a known client, so write like a person, not a "
        "newsletter template. OUTPUT CONTRACT: put the subject line in the "
        "'subject' field and the email body in 'content'. Subject: under 60 "
        "chars, plain words, no "
        "clickbait punctuation, no emoji, no 'newsletter'/'update' filler — it "
        "names what the reader gets. Body: open with a one-line hook that names "
        "a situation the reader recognizes (use {{first_name}} for the greeting "
        "if the brief wants one), one core idea fully explained with real facts "
        "and numbers only — every number MUST come from the brief, inventing "
        "statistics or client stories fails the draft — then ONE clear CTA with a "
        "real URL from the brief. Scannable: short paragraphs or a short "
        "numbered list, under ~250 words. Sign off '— Themis @ Cloudless' "
        "unless told otherwise. No hashtags, no emojis in the subject, at most "
        "one in the body. The unsubscribe footer is added automatically — never "
        "write one. Plain everyday English."
    ),
}

HASHTAG_RULES = """\
HASHTAG RULES (critical for 2026 platform algorithms):
- Choose hashtags that are semantically relevant to the post content. A disconnect between
  text and hashtags harms distribution.
- Prefer niche and mid-tier hashtags (10K-500K posts) over mega-tags — they outperform 3:1
  on reach-to-engagement ratio because your content can actually compete there.
- Include one branded hashtag (e.g. #cloudless) when relevant.
- Never copy-paste the same hashtag block across posts — platforms penalize this as spam.
- Respect each platform's hashtag cap (see platform guidelines above)."""

CONTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "content": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "suggested_media": {"type": ["string", "null"]},
    },
    "required": ["content", "hashtags", "suggested_media"],
}

# Email campaigns get a dedicated schema — the subject is a first-class field
# (small models can't be trusted to keep a "SUBJECT:" line inside `content`).
EMAIL_SCHEMA = {
    "type": "object",
    "properties": {
        "subject": {"type": "string"},
        "content": {"type": "string"},
        "suggested_media": {"type": ["string", "null"]},
    },
    "required": ["subject", "content", "suggested_media"],
}
