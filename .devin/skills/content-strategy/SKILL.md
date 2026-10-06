---
name: content-strategy
description: >-
  Content strategy layer: SEO/hashtag scoring rules and the Sofia Kakkava Creator Type voice (Expert/Storyteller/Energizer formula stored in brand_voices.voice_signature). Use when tuning post voice, platform targets, or scoring quality.
---

# Content Strategy

Consolidated skill — each section below was a standalone skill. Member scripts/templates live under `<source-skill>/` inside this directory.

| Section | Source |
|---|---|
| Content Scoring & Hashtag Strategy | `content-strategy` |
| Creator Type voice (Visibility Era DAY 1) | `content-strategy` → `creator-type-voice/` |

## Content Scoring & Hashtag Strategy

Patterns for scoring generated social media content and building tiered hashtag strategies.
Based on research from PulseTag, contentflow, and Marketing Orchestrator.

### Content Scoring

Score generated content on four dimensions before publishing. Each dimension returns 0-100.

#### Readability (0-100)
- Flesch reading ease score
- Sentence length variance
- Paragraph structure
- Target: 60+ (easy to read for general audience)

#### Engagement (0-100)
- Question presence (boosts comments)
- CTA presence (boosts clicks)
- Emotional words count
- Personal pronouns (I, you, we)
- Target: 70+

#### Hashtag Quality (0-100)
- Count within platform limits (LinkedIn: 3-5, Instagram: 10-30, Twitter: 1-3)
- Mix of broad and niche tags
- No banned/spammy hashtags
- Relevance to content
- Target: 80+

#### Length Fit (0-100)
- Within platform character limits:
  - Twitter/X: 280 chars
  - LinkedIn: 3000 chars (1300 for feed-optimized)
  - Instagram: 2200 chars
  - Threads: 500 chars
  - Facebook: 63206 chars
  - TikTok: 2200 chars
- Target: 90+ (well within limits, not too short either)

#### Overall Score
```
overall = (readability * 0.25 + engagement * 0.30 + hashtag_quality * 0.25 + length_fit * 0.20)
```

### Three-Tier Hashtag Strategy

From PulseTag research. Instead of a flat hashtag list, categorize into three tiers:

#### Tier 1: Safe (High-Volume)
- 100K+ posts using this tag
- Broad reach, high competition
- 1-3 tags per post
- Example: `#cloud`, `#startup`, `#marketing`

#### Tier 2: Rising (Trending Mid-Volume)
- 10K-100K posts
- Current relevance, moderate competition
- 2-4 tags per post
- Example: `#serverless`, `#aimarketing`, `#cloudnative`

#### Tier 3: Niche (Low-Competition)
- <10K posts
- High-intent audience, low competition
- 3-5 tags per post
- Example: `#serverlesscloud`, `#cloudlessgr`, `#aisocialmedia`

#### Platform Mix
| Platform | Safe | Rising | Niche | Total |
|----------|------|--------|-------|-------|
| LinkedIn | 1 | 2 | 2 | 3-5 |
| Instagram | 3 | 5 | 10 | 10-20 |
| Twitter/X | 0-1 | 1 | 1 | 1-3 |
| Threads | 1 | 2 | 2 | 3-5 |
| TikTok | 2 | 3 | 5 | 8-15 |
| Facebook | 1 | 1 | 1 | 1-3 |

### Implementation in SocialAuto

SocialAuto already has:
- SEO scoring in `app/services/media_quality.py`
- Hashtag generation in `app/api/ai.py`
- NLP plain-English check/fix
- Spellcheck via LanguageTool

#### Recommended Additions

```python
## In app/services/content_scorer.py
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

### Free/Open-Source Tools Referenced

- **PulseTag**: https://github.com/bradmca/pulse-tag — AI-driven hashtag generator with
  three-tier strategy. Uses free OpenRouter LLMs.
- **contentflow**: https://github.com/teyfikoz/contentflow — Multi-platform content
  generator with scoring. Works offline with 50+ templates or online with HuggingFace AI.
- **Marketing Orchestrator**: https://github.com/Dakshaarvind/Marketing-Orchestator —
  4-stage AI pipeline with SEO scoring (85-90/100 typical).

## Creator Type voice (Visibility Era DAY 1)

### When to use

- Generated posts should match the owner's communication style
- Re-running the DAY 1 self-assessment after a re-positioning
- Switching creator type (expert / storyteller / energizer / blend)

### How it's wired

`POST /api/v1/ai/generate-content` loads `Brand` + `BrandVoice` and injects
`voice_signature` into the system prompt via
`build_brand_system_prompt` (app/services/brand_compliance.py). Three keys
carry the adoption:

- `creator_type` — the type name/blend
- `post_formula` — the numbered structure every post should follow
- `style_notes` — tone guardrails

Every n8n workflow and the UI generator flows through this path — one
`PUT /api/v1/brand/voice` changes all of them.

### DAY 2 — The 2-Platform Rule (adopted)

Owner's platform commitment (8 weeks): **MAIN = LinkedIn**, **SECONDARY =
all Meta platforms** (Instagram, Facebook Page, Threads — adapted versions
of the main content), **LAST = everything else** (Twitter/X, TikTok —
opportunistic only). Stored as `voice_signature.platform_focus`.

How it maps to the live workflows:

| Workflow | Trigger | Tier | Notes |
|---|---|---|---|
| `cloudless-carousel-pipeline` | every 2 days 19:00 EET | main | LinkedIn carousel → Company Page `9c4451bb-…` ✓ |
| `weekly-cloud-computing-post` | Mon 09:00 | main | resolves `CLOUDLESS_LINKEDIN_ORG_ACCOUNT_ID` ✓ |
| `marketing-image-generation` | every 24h | secondary | retargeted Twitter→Instagram `38ddbd44-…` (X quota dead; IG needs images anyway) |
| `socialauto-daily-slack-digest` | daily 09:00 | n/a | reporting only |
| all `*-text-post` / `*-image-post` | webhook only | varies | fire on demand, not scheduled |

Rule of thumb when editing scheduled workflows: scheduled/original content
targets LinkedIn; adapted content targets Meta; never add a new schedule
that fires into `last`-tier platforms.

### DAY 3 — Own Your Lane (adopted)

The niche formula — "I help [WHO] who [PROBLEM] so they can [RESULT]" —
stored as `voice_signature.niche` so every generation is audience-anchored:

> I help small teams and small business owners who waste time and money on
> servers and cloud complexity so they can run on simple, cost-efficient
> cloud that just works.

If generated content drifts generic or addresses the wrong audience, check
`niche` first — it is the WHO anchor for every post.

### DAY 4+5 — Profile test + Bio That Sells (adopted)

DAY 4 audit runs via `social-profile-update` skill (`scripts/profile_audit.py`).
2026-09-21 scores: FB Pages 8/8, Twitter 8/8, Threads 7/8, TikTok 7/8,
IG 5/8. Residual fails are platform limits, not copy: IG name/website are
app-only, Threads/TikTok have no writable website field, avatar vision
notes are report-only (never change avatars without explicit ask).

DAY 5 bio structure stored as `voice_signature.bio_formula` —
[WHO+RESULT] / [METHOD or PROOF] / [REASON TO FOLLOW] / [CTA], compressed.
Applied to FB Pages (about ≤100 chars), Twitter bio, Threads bio (via
bridge), IG bio (via bridge edit form). TikTok bio is captcha-blocked;
LinkedIn headline/about updates wait for the sidecar 429 circuit to clear.

Threads data fix (2026-09-21): `social_accounts.username` was stale
(`cloudless_gr` → corrected to `cloudless.gr`) — the bridge navigates
`threads.com/@{username}` for reads/writes, and the wrong handle 302'd to
a page with no Edit button.

### Tool

```bash
## See the live voice_signature
.devin/skills/content-strategy/creator-type-voice/scripts/creator_type.py show

## Interactive DAY 1 assessment (3 questions → type)
.devin/skills/content-strategy/creator-type-voice/scripts/creator_type.py quiz

## Apply a type to the brand voice (merges, doesn't wipe other keys)
creator_type.py apply expert|storyteller|energizer|blend

## Show the DAY 2 platform tiers + live platform_focus
creator_type.py platforms

## Generate a sample post to check the style
creator_type.py verify [platform]
```

Auth: TOTP login against the admin account inside `social-api` (same flow
as the n8n workflows + socialauto MCP — no secret in the script).

### The three types (DAY 1)

| Type | Builds trust through | Post shape |
|---|---|---|
| **Expert** | Knowledge | Problem → clear steps/framework → action question |
| **Storyteller** | Honesty | Real moment/tension → what happened → honest takeaway → inviting question |
| **Energizer** | Boldness | Bold true claim → one proof point → "do this today" momentum |

The admin's result was a mixed A+B+C → the `blend` profile (Expert-led:
teach concretely, ground in something real, close with energy). `blend` is
the current applied state.

### Guardrails

- `post_formula` forbids **invented anecdotes** — "true observation, client
  lesson, or behind-the-scenes detail" only.
- Keep formulas compact: `voice_signature` is dumped whole into the system
  prompt; long prose wastes tokens.
- If generated content drifts generic, check `creator_type.py verify`
  output and tighten `style_notes` — small DMR models follow short concrete
  rules better than abstract guidance.

### Related

- `socialauto-brand` — brand DNA, tone dimensions, messaging pillars
- `social-profile-update` — DAY 4 checklist (profile completeness audit)
- `publish-ops` — alert signature runbook
- PDFs (source material): `OneDrive/Kakkava Sofia/DAY {1-5} *.pdf`
