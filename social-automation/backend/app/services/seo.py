"""SEO and content optimization helpers.

Provides keyword extraction, meta title/description suggestions, content
scoring, and actionable readability/plain-English recommendations. Designed to
run cheaply on free-tier inference (Cloudflare Workers AI or Ollama) and to
re-use the existing ``plain_english`` and ``spellcheck`` modules.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from string import punctuation
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.services.inference import call_inference
from app.services.plain_english import check_plain_english

settings = get_settings()

# Simple English stop-words list; avoids NLTK/spacy dependency.
_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "is", "are", "was", "were", "be",
    "been", "being", "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "must", "shall", "can", "need", "dare",
    "ought", "used", "to", "of", "in", "for", "on", "with", "at", "from", "as",
    "into", "through", "during", "before", "after", "above", "below", "between",
    "under", "again", "further", "then", "once", "here", "there", "when", "where",
    "why", "how", "all", "any", "both", "each", "few", "more", "most", "other",
    "some", "such", "no", "nor", "not", "only", "own", "same", "so", "than", "too",
    "very", "just", "now", "this", "that", "these", "those", "i", "me", "my",
    "myself", "we", "our", "ours", "ourselves", "you", "your", "yours", "yourself",
    "yourselves", "he", "him", "his", "himself", "she", "her", "hers", "herself",
    "it", "its", "itself", "they", "them", "their", "theirs", "themselves", "what",
    "which", "who", "whom", "whose", "this", "that", "am", "are", "is", "was", "were",
}

# Platform-specific best-practice length ranges.
_PLATFORM_HINTS: dict[str, dict[str, int | tuple[int, int] | None]] = {
    "twitter": {"max_chars": 280, "ideal_hashtags": (1, 3), "title_len": (0, 0), "meta_desc_len": (0, 0)},
    "linkedin": {"max_chars": 3000, "ideal_hashtags": (3, 8), "title_len": (40, 70), "meta_desc_len": (120, 160)},
    "instagram": {"max_chars": 2200, "ideal_hashtags": (5, 10), "title_len": (0, 0), "meta_desc_len": (0, 0)},
    "facebook": {"max_chars": 63206, "ideal_hashtags": (0, 3), "title_len": (40, 70), "meta_desc_len": (120, 160)},
    "threads": {"max_chars": 500, "ideal_hashtags": (0, 2), "title_len": (0, 0), "meta_desc_len": (0, 0)},
    "tiktok": {"max_chars": 2200, "ideal_hashtags": (3, 5), "title_len": (0, 0), "meta_desc_len": (0, 0)},
}


def _tokens(text: str) -> list[str]:
    """Lower-case, punctuation-stripped word tokens."""
    return [
        word.strip(punctuation).lower()
        for word in re.split(r"\s+", text)
        if word.strip(punctuation)
    ]


def _clean_keyword(word: str) -> str | None:
    word = word.strip(punctuation).lower()
    if not word or len(word) < 3 or word in _STOPWORDS or word.isdigit():
        return None
    return word


def extract_keywords(text: str, max_keywords: int = 10) -> list[dict[str, Any]]:
    """Extract the most frequent content keywords with counts."""
    counts: Counter[str] = Counter()
    for phrase in re.split(r"[.!?;\n]+", text):
        seen_in_phrase: set[str] = set()
        for word in _tokens(phrase):
            cleaned = _clean_keyword(word)
            if cleaned and cleaned not in seen_in_phrase:
                counts[cleaned] += 1
                seen_in_phrase.add(cleaned)

    return [
        {"keyword": word, "count": count}
        for word, count in counts.most_common(max_keywords)
    ]


def _avg_sentence_words(text: str) -> float:
    sentences = [s.strip() for s in re.split(r"[.!?]+", text) if s.strip()]
    if not sentences:
        return 0.0
    return sum(len(s.split()) for s in sentences) / len(sentences)


def _count_hashtags(text: str) -> int:
    return len(re.findall(r"#\w+", text))


def _count_links(text: str) -> int:
    return len(re.findall(r"https?://\S+", text))


@dataclass
class ContentScoreReport:
    overall: int = 0
    readability: int = 0
    keywords: int = 0
    hashtags: int = 0
    links: int = 0
    plain_english: int = 0
    length: int = 0
    recommendations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def score_content(text: str, platform: str = "linkedin") -> ContentScoreReport:
    """Return a 0-100 SEO-style content score with recommendations."""
    report = ContentScoreReport()
    hints = _PLATFORM_HINTS.get(platform.lower(), _PLATFORM_HINTS["linkedin"])
    max_chars = int(hints.get("max_chars") or 3000)
    ideal_hashtags = hints.get("ideal_hashtags") or (3, 8)

    char_count = len(text)
    if char_count == 0:
        report.recommendations.append("Add content before scoring.")
        return report

    # Length score: prefer 80-100% of platform max, but not over.
    ratio = char_count / max_chars
    if ratio > 1:
        report.length = max(0, 100 - int((ratio - 1) * 100))
        report.recommendations.append(
            f"This post is {char_count} characters, over the {platform} limit of {max_chars}."
        )
    elif ratio < 0.2:
        report.length = int(ratio * 500)  # 0..100
        report.recommendations.append(
            f"Content is short ({char_count} chars). Aim for at least {int(max_chars * 0.2)} characters."
        )
    else:
        report.length = 100

    # Readability score based on average sentence length.
    avg = _avg_sentence_words(text)
    if avg <= 15:
        report.readability = 100
    elif avg <= 20:
        report.readability = 80
    elif avg <= 25:
        report.readability = 60
    else:
        report.readability = 40
        report.recommendations.append(
            f"Sentences average {avg:.1f} words. Shorter sentences improve readability."
        )

    # Hashtag score.
    h_count = _count_hashtags(text)
    h_min, h_max = ideal_hashtags
    if h_min <= h_count <= h_max:
        report.hashtags = 100
    elif h_count < h_min:
        report.hashtags = max(0, 100 - (h_min - h_count) * 25)
        report.recommendations.append(
            f"Add {h_min - h_count} more hashtag(s) for {platform}."
        )
    else:
        report.hashtags = max(0, 100 - (h_count - h_max) * 15)
        report.recommendations.append(
            f"Reduce hashtags to {h_max} or fewer for {platform}."
        )

    # Link score: one link is ideal for LinkedIn/Facebook, none for Twitter/Threads/Instagram/TikTok.
    l_count = _count_links(text)
    if platform in ("twitter", "threads", "instagram", "tiktok"):
        report.links = 100 if l_count == 0 else 50
    else:
        if l_count == 1:
            report.links = 100
        elif l_count == 0:
            report.links = 70
            report.recommendations.append("Add a relevant link for better engagement.")
        else:
            report.links = 60
            report.recommendations.append("Use at most one link to keep focus.")

    # Plain-English score.
    issues = check_plain_english(text)
    if not issues:
        report.plain_english = 100
    else:
        # Each issue category costs 20 points.
        report.plain_english = max(0, 100 - len(issues) * 20)
        jargon = [m for i in issues for m in (i.matches or [])]
        if jargon:
            report.recommendations.append(
                f"Replace jargon/buzzwords: {', '.join(jargon[:5])}."
            )
        if any(i.reason == "long_sentences" for i in issues):
            report.recommendations.append("Some sentences are too long for plain English.")

    # Keyword score: at least a few keywords present.
    kw = extract_keywords(text, max_keywords=20)
    if len(kw) >= 3:
        report.keywords = 100
    elif kw:
        report.keywords = len(kw) * 30
    else:
        report.keywords = 0
        report.recommendations.append("No clear keywords found. Add a focused topic.")

    # Overall weighted score.
    report.overall = int(
        report.length * 0.15
        + report.readability * 0.25
        + report.plain_english * 0.25
        + report.hashtags * 0.15
        + report.links * 0.10
        + report.keywords * 0.10
    )
    return report


async def suggest_meta(title: str | None, body: str, platform: str = "linkedin") -> dict[str, str]:
    """Suggest meta title and description for a LinkedIn article or blog post."""
    if platform in ("twitter", "threads", "instagram"):
        return {"title": "", "description": ""}

    hints = _PLATFORM_HINTS.get(platform.lower(), _PLATFORM_HINTS["linkedin"])
    title_range_raw = hints.get("title_len")
    title_range: tuple[int, int] = title_range_raw if isinstance(title_range_raw, tuple) else (40, 70)
    desc_range_raw = hints.get("meta_desc_len")
    desc_range: tuple[int, int] = desc_range_raw if isinstance(desc_range_raw, tuple) else (120, 160)

    prompt = f"""Given this content, write a concise SEO title and meta description.

Title hint: {title or '(none)'}
Body (first 500 chars): {body[:500]}

Constraints:
- Title between {title_range[0]}-{title_range[1]} characters.
- Description between {desc_range[0]}-{desc_range[1]} characters.
- No buzzwords, plain English, one clear benefit.

Return JSON with:
- title
- description"""

    schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "description": {"type": "string"},
        },
        "required": ["title", "description"],
    }

    try:
        result = await call_inference(prompt, provider_name="cloudflare", schema=schema)
        return {
            "title": str(result.get("title", "")).strip(),
            "description": str(result.get("description", "")).strip(),
        }
    except (httpx.HTTPError, Exception):
        # Fail cheaply: return an empty suggestion rather than break the editor.
        return {"title": "", "description": ""}


async def analyze_seo(
    text: str,
    platform: str = "linkedin",
    title: str | None = None,
    db: AsyncSession | None = None,
    team_id: Any | None = None,
) -> dict[str, Any]:
    """Full SEO analysis: keywords, score, meta, and recommendations."""
    score = score_content(text, platform)
    meta = await suggest_meta(title, text, platform)
    keywords = extract_keywords(text)
    return {
        "platform": platform,
        "score": score.to_dict(),
        "keywords": keywords,
        "meta": meta,
        "character_count": len(text),
        "hashtag_count": _count_hashtags(text),
        "link_count": _count_links(text),
    }
