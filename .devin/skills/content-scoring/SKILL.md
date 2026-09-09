# Content Scoring & Hashtag Strategy

Patterns for scoring generated social media content and building tiered hashtag strategies.
Based on research from PulseTag, contentflow, and Marketing Orchestrator.

## Content Scoring

Score generated content on four dimensions before publishing. Each dimension returns 0-100.

### Readability (0-100)
- Flesch reading ease score
- Sentence length variance
- Paragraph structure
- Target: 60+ (easy to read for general audience)

### Engagement (0-100)
- Question presence (boosts comments)
- CTA presence (boosts clicks)
- Emotional words count
- Personal pronouns (I, you, we)
- Target: 70+

### Hashtag Quality (0-100)
- Count within platform limits (LinkedIn: 3-5, Instagram: 10-30, Twitter: 1-3)
- Mix of broad and niche tags
- No banned/spammy hashtags
- Relevance to content
- Target: 80+

### Length Fit (0-100)
- Within platform character limits:
  - Twitter/X: 280 chars
  - LinkedIn: 3000 chars (1300 for feed-optimized)
  - Instagram: 2200 chars
  - Threads: 500 chars
  - Facebook: 63206 chars
  - TikTok: 2200 chars
- Target: 90+ (well within limits, not too short either)

### Overall Score
```
overall = (readability * 0.25 + engagement * 0.30 + hashtag_quality * 0.25 + length_fit * 0.20)
```

## Three-Tier Hashtag Strategy

From PulseTag research. Instead of a flat hashtag list, categorize into three tiers:

### Tier 1: Safe (High-Volume)
- 100K+ posts using this tag
- Broad reach, high competition
- 1-3 tags per post
- Example: `#cloud`, `#startup`, `#marketing`

### Tier 2: Rising (Trending Mid-Volume)
- 10K-100K posts
- Current relevance, moderate competition
- 2-4 tags per post
- Example: `#serverless`, `#aimarketing`, `#cloudnative`

### Tier 3: Niche (Low-Competition)
- <10K posts
- High-intent audience, low competition
- 3-5 tags per post
- Example: `#serverlesscloud`, `#cloudlessgr`, `#aisocialmedia`

### Platform Mix
| Platform | Safe | Rising | Niche | Total |
|----------|------|--------|-------|-------|
| LinkedIn | 1 | 2 | 2 | 3-5 |
| Instagram | 3 | 5 | 10 | 10-20 |
| Twitter/X | 0-1 | 1 | 1 | 1-3 |
| Threads | 1 | 2 | 2 | 3-5 |
| TikTok | 2 | 3 | 5 | 8-15 |
| Facebook | 1 | 1 | 1 | 1-3 |

## Implementation in SocialAuto

SocialAuto already has:
- SEO scoring in `app/services/media_quality.py`
- Hashtag generation in `app/api/ai.py`
- NLP plain-English check/fix
- Spellcheck via LanguageTool

### Recommended Additions

```python
# In app/services/content_scorer.py
from dataclasses import dataclass

@dataclass
class ContentScore:
    readability: float
    engagement: float
    hashtag_quality: float
    length_fit: float

    @property
    def overall(self) -> float:
        return (
            self.readability * 0.25
            + self.engagement * 0.30
            + self.hashtag_quality * 0.25
            + self.length_fit * 0.20
        )

PLATFORM_LIMITS = {
    "twitter": 280,
    "linkedin": 3000,
    "instagram": 2200,
    "threads": 500,
    "facebook": 63206,
    "tiktok": 2200,
}

def score_content(text: str, platform: str, hashtags: list[str]) -> ContentScore:
    """Score content on readability, engagement, hashtag quality, and length fit."""
    return ContentScore(
        readability=_score_readability(text),
        engagement=_score_engagement(text),
        hashtag_quality=_score_hashtags(hashtags, platform),
        length_fit=_score_length(text, platform),
    )
```

## Free/Open-Source Tools Referenced

- **PulseTag**: https://github.com/bradmca/pulse-tag — AI-driven hashtag generator with
  three-tier strategy. Uses free OpenRouter LLMs.
- **contentflow**: https://github.com/teyfikoz/contentflow — Multi-platform content
  generator with scoring. Works offline with 50+ templates or online with HuggingFace AI.
- **Marketing Orchestrator**: https://github.com/Dakshaarvind/Marketing-Orchestator —
  4-stage AI pipeline with SEO scoring (85-90/100 typical).
