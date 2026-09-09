"""Content scoring for social media posts.

Scores generated content on four dimensions: readability, engagement,
hashtag quality, and length fit. Also provides a three-tier hashtag
strategy (Safe/Rising/Niche) based on PulseTag research.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

logger = __import__("logging").getLogger(__name__)

# Platform character limits
PLATFORM_LIMITS: dict[str, int] = {
    "twitter": 280,
    "linkedin": 3000,
    "instagram": 2200,
    "threads": 500,
    "facebook": 63206,
    "tiktok": 2200,
}

# Platform hashtag recommendations (safe, rising, niche, total)
PLATFORM_HASHTAG_MIX: dict[str, dict[str, int]] = {
    "linkedin": {"safe": 1, "rising": 2, "niche": 2, "total": 5},
    "instagram": {"safe": 3, "rising": 5, "niche": 10, "total": 20},
    "twitter": {"safe": 0, "rising": 1, "niche": 1, "total": 3},
    "threads": {"safe": 1, "rising": 2, "niche": 2, "total": 5},
    "tiktok": {"safe": 2, "rising": 3, "niche": 5, "total": 15},
    "facebook": {"safe": 1, "rising": 1, "niche": 1, "total": 3},
}

# Emotional/engagement words
EMOTIONAL_WORDS = frozenset({
    "amazing", "incredible", "exciting", "love", "thrilled", "delighted",
    "fantastic", "awesome", "wonderful", "breakthrough", "game-changer",
    "transformative", "revolutionary", "stunning", "remarkable", "powerful",
    "essential", "critical", "proven", "guaranteed", "exclusive", "limited",
    "free", "new", "instant", "easy", "simple", "fast", "best", "top",
})

# Personal pronouns for engagement scoring
PERSONAL_PRONOUNS = frozenset({"i", "you", "we", "your", "our", "us", "me", "my", "us"})

# CTA phrases
CTA_PHRASES = frozenset({
    "click", "subscribe", "follow", "share", "comment", "learn more",
    "get started", "sign up", "try", "download", "join", "discover",
    "explore", "read more", "check out", "don't miss", "act now",
})


@dataclass
class ContentScore:
    """Four-dimension content score."""
    readability: float
    engagement: float
    hashtag_quality: float
    length_fit: float

    @property
    def overall(self) -> float:
        """Weighted overall score (0-100)."""
        return (
            self.readability * 0.25
            + self.engagement * 0.30
            + self.hashtag_quality * 0.25
            + self.length_fit * 0.20
        )

    def to_dict(self) -> dict[str, float]:
        return {
            "readability": round(self.readability, 1),
            "engagement": round(self.engagement, 1),
            "hashtag_quality": round(self.hashtag_quality, 1),
            "length_fit": round(self.length_fit, 1),
            "overall": round(self.overall, 1),
        }


@dataclass
class HashtagStrategy:
    """Three-tier hashtag strategy."""
    safe: list[str] = field(default_factory=list)
    rising: list[str] = field(default_factory=list)
    niche: list[str] = field(default_factory=list)

    @property
    def all_tags(self) -> list[str]:
        return self.safe + self.rising + self.niche

    def to_dict(self) -> dict[str, list[str]]:
        return {
            "safe": self.safe,
            "rising": self.rising,
            "niche": self.niche,
        }


def _flesch_reading_ease(text: str) -> float:
    """Calculate Flesch Reading Ease score (0-100)."""
    sentences = max(len(re.split(r"[.!?]+", text)) - 1, 1)
    words = len(text.split())
    if words == 0:
        return 0.0
    syllables = sum(_count_syllables(word) for word in text.split())
    if sentences == 0:
        return 0.0
    score = 206.835 - 1.015 * (words / sentences) - 84.6 * (syllables / words)
    return max(0.0, min(100.0, score))


def _count_syllables(word: str) -> int:
    """Estimate syllable count for a word."""
    word = word.lower().strip(".,!?;:\"'()[]{}")
    if not word:
        return 0
    count = 0
    vowels = "aeiouy"
    prev_vowel = False
    for ch in word:
        is_vowel = ch in vowels
        if is_vowel and not prev_vowel:
            count += 1
        prev_vowel = is_vowel
    if word.endswith("e") and count > 1:
        count -= 1
    return max(count, 1)


def _score_readability(text: str) -> float:
    """Score readability (0-100)."""
    if not text.strip():
        return 0.0
    return _flesch_reading_ease(text)


def _score_engagement(text: str) -> float:
    """Score engagement potential (0-100)."""
    if not text.strip():
        return 0.0
    text_lower = text.lower()
    words = text_lower.split()
    if not words:
        return 0.0

    score = 50.0  # Base score

    # Question presence (+15)
    if "?" in text:
        score += 15

    # CTA presence (+15)
    for cta in CTA_PHRASES:
        if cta in text_lower:
            score += 15
            break

    # Emotional words (+5 each, max +20)
    emotional_count = sum(1 for w in words if w.strip(".,!?;:") in EMOTIONAL_WORDS)
    score += min(emotional_count * 5, 20)

    # Personal pronouns (+2 each, max +10)
    pronoun_count = sum(1 for w in words if w.strip(".,!?;:").lower() in PERSONAL_PRONOUNS)
    score += min(pronoun_count * 2, 10)

    # Exclamation marks (+5, max +10)
    exclamations = text.count("!")
    score += min(exclamations * 5, 10)

    # Numbers/stats (+10) — linear scan (avoid polynomial ReDoS on digit runs)
    if _has_stats_signal(text):
        score += 10

    return min(score, 100.0)


def _has_stats_signal(text: str) -> bool:
    """Detect %, currency, or 4-digit year-like tokens without regex."""
    for i, ch in enumerate(text):
        if ch == "%" and i > 0 and text[i - 1].isdigit():
            return True
        if ch == "$" and i + 1 < len(text) and text[i + 1].isdigit():
            return True
    for token in text.split():
        stripped = token.strip(".,!?;:")
        if len(stripped) == 4 and stripped.isdigit():
            return True
    return False


def _score_hashtags(hashtags: list[str], platform: str) -> float:
    """Score hashtag quality (0-100)."""
    if not hashtags:
        return 50.0  # Neutral if no hashtags

    score = 50.0
    mix = PLATFORM_HASHTAG_MIX.get(platform, PLATFORM_HASHTAG_MIX["linkedin"])
    total_limit = mix["total"]

    # Count within limits (+20)
    if len(hashtags) <= total_limit:
        score += 20
    else:
        # Penalize over-limit
        over = len(hashtags) - total_limit
        score -= min(over * 5, 30)

    # Hashtag format quality (+10)
    valid_format = all(re.match(r"^#[a-zA-Z0-9_]+$", h) for h in hashtags)
    if valid_format:
        score += 10

    # Diversity (unique tags) (+10)
    unique = len(set(h.lower() for h in hashtags))
    if unique == len(hashtags):
        score += 10

    # Length of hashtags (not too long, not too short) (+10)
    good_length = sum(1 for h in hashtags if 3 <= len(h) <= 30)
    if hashtags and good_length / len(hashtags) > 0.8:
        score += 10

    return min(max(score, 0.0), 100.0)


def _score_length(text: str, platform: str) -> float:
    """Score length fit (0-100)."""
    limit = PLATFORM_LIMITS.get(platform, 2200)
    length = len(text)

    if length == 0:
        return 0.0

    if length >= limit:
        # At or over limit — heavily penalize
        return max(0.0, 50.0 - (length - limit + 1) / limit * 50)

    # Optimal range: 50%-90% of limit
    ratio = length / limit
    if 0.5 <= ratio <= 0.9:
        return 100.0
    elif 0.3 <= ratio < 0.5:
        return 80.0
    elif 0.1 <= ratio < 0.3:
        return 60.0
    elif ratio > 0.9:
        return 90.0
    else:
        return 40.0


def score_content(text: str, platform: str, hashtags: list[str] | None = None) -> ContentScore:
    """Score content on readability, engagement, hashtag quality, and length fit.

    Args:
        text: Post text content.
        platform: Platform name (linkedin, twitter, instagram, threads, facebook, tiktok).
        hashtags: List of hashtag strings (e.g., ["#cloud", "#startup"]).

    Returns:
        ContentScore with four dimensions and an overall weighted score.
    """
    tags = hashtags or []
    return ContentScore(
        readability=_score_readability(text),
        engagement=_score_engagement(text),
        hashtag_quality=_score_hashtags(tags, platform),
        length_fit=_score_length(text, platform),
    )


def build_hashtag_strategy(
    topic: str,
    platform: str,
    existing_tags: list[str] | None = None,
) -> HashtagStrategy:
    """Build a three-tier hashtag strategy for a topic.

    This is a heuristic-based strategy. For AI-powered hashtag generation,
    use the /api/v1/ai/generate-hashtags endpoint.

    Args:
        topic: The content topic (e.g., "serverless cloud architecture").
        platform: Target platform.
        existing_tags: Already-selected hashtags to include.

    Returns:
        HashtagStrategy with safe, rising, and niche tag lists.
    """
    tags = existing_tags or []
    words = [w.lower().strip(".,!?;:") for w in topic.split() if len(w) > 2]

    strategy = HashtagStrategy()

    # Safe: broad, high-volume tags from topic keywords
    if words:
        strategy.safe = [f"#{words[0]}"]
    if len(words) > 1:
        strategy.safe.append(f"#{words[1]}")

    # Rising: compound tags from topic
    if len(words) >= 2:
        strategy.rising = [f"#{words[0]}{words[1]}", f"#{words[1]}{words[0]}"]
    if len(words) >= 3:
        strategy.rising.append(f"#{words[2]}")

    # Niche: specific long-tail tags
    if len(words) >= 2:
        niche_tag = "#" + "".join(words[:3])
        strategy.niche = [niche_tag]
    if len(words) >= 4:
        strategy.niche.append(f"#{words[3]}{words[0]}")

    # Add existing tags to appropriate tier
    for tag in tags:
        if tag not in strategy.all_tags:
            if len(strategy.niche) < 10:
                strategy.niche.append(tag)

    # Trim to platform limits
    mix = PLATFORM_HASHTAG_MIX.get(platform, PLATFORM_HASHTAG_MIX["linkedin"])
    strategy.safe = strategy.safe[: mix["safe"]]
    strategy.rising = strategy.rising[: mix["rising"]]
    strategy.niche = strategy.niche[: mix["niche"]]

    return strategy
