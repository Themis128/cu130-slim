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
| VEC 2.0 challenge spaces (Skool + Telegram) | `content-strategy` |

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

### DAY 12 — The Post Blueprint (adopted)

Every post follows the 4-part structure, stored as
`voice_signature.post_blueprint` (+ `blueprint_mistakes`) so it injects
into `/api/v1/ai/generate-content` and all n8n workflows alongside the
creator-type formula. The blueprint is the *skeleton*; `post_formula` is
the *flavor* inside it.

1. **HOOK** — the very first line stops the scroll: a specific situation
   the reader recognizes; never reveal the answer up front.
2. **CONTEXT** — 2–3 lines that make the reader feel seen; show
   understanding before teaching anything.
3. **VALUE** — ONE clear insight fully explained (3–5 lines); never a
   list of tips — one thing lands harder than five scattered.
4. **CTA** — one specific question or direction, ≤2 lines; invite
   conversation, don't push.

Checklist before publishing: does the first line stop the scroll? Does
context make them feel seen? One clear insight, not a list? CTA invites,
not pushes? Does every line earn the next (if you'd skip it, cut it)?

Forbidden blueprint mistakes: skipping context (hook → value directly),
multi-tip value sections, vague "let me know what you think" CTAs,
burying the hook ("I've been thinking…" openers), writing for yourself
instead of the reader.

Apply/inspect live: `creator_type.py blueprint` (show) /
`creator_type.py blueprint apply` (writes `post_blueprint` +
`blueprint_mistakes` into voice_signature).

DAY 11 source (hooks — blueprint part 1) also lives in
`3 week/Day11/…pdf` (Hook Master Guide: pain / specific-number /
effort-vs-result / curiosity / contrast / callout / hard-truth /
competitor hook types; kill patterns: too generic, reveals the answer,
vague questions, no specific situation, never testing, topic-hopping).

### VEC 2.0 — where the challenge actually lives

The Visibility Era Challenge 2.0 runs on **Skool + Telegram**, not on the
PDFs alone. Mapped 2026-10-08.

**Skool community** — `https://www.skool.com/sofia-kakkava-coaching`
(free, ~216 members). Tabs: Community / Classroom / Calendar / Members /
Leaderboards / About.

- **Login**: email+password at `skool.com/login`. Driven successfully via
  the browser-novnc bridge — `/session/fill` on `input[type=email]` +
  `input[type=password]`, then `/session/click` `button[type=submit]`.
  Session persists in the `browser_profile` volume, so later drops are
  automated. Credentials are user-provided per session — not stored in
  `.env` or the DB.
- **Classroom**: course id `0322497b`. Module URL format
  `/sofia-kakkava-coaching/classroom/0322497b?md=<module-id>`. Week
  headers are collapsed `div`s — click to expand, then read
  `a[href*=md=]` links.
- **Community feed categories** (the full set — there is no Spotlight
  category on Skool): Announcements, Member Intros, General discussion,
  Wins & Breakthroughs, Ask Sofia, Free Resources.
- **Search box is React-controlled** — programmatic fill + synthetic
  Enter does not open results; needs real keystrokes or direct URL work.

**Telegram spaces** — daily-task destinations live in a **private
Telegram supergroup**, `t.me/c/3761067037` (chat_id `-1003761067037`),
as forum topics:

| Topic | t.me/c/3761067037/N | Purpose |
|---|---|---|
| main / ask | /1, /2 | general + "ask it here" Q&A |
| Visibility HQ | /5 | command center; pinned post explains all spaces |
| Journey Highlights | /7 | before/after screenshots (DAY-1 "before" → DAY-40 "after") |
| Wins | /8 | wins & progress |

- **Visibility Spotlight** = the topic where the daily "drop your post
  link or screenshot" task lands (mentioned in nearly every DAY lesson,
  never hyperlinked — exact thread_id still unmapped; candidates outside
  the listed IDs, e.g. /3 /4 /6 /9).
- **Visibility Hub** = the community-chat topic used for the daily
  "COMMUNITY ACTION" step (comment on other members' links).
- ⚠️ `Cloudless_newbot` is **NOT a member of this group** — the bot's
  "Visibility Era 2.0" system prompt refers to a different group, and
  `getChat`/`sendChatAction` on `-1003761067037` return `chat not
  found`. Spotlight drops therefore need the **user's personal Telegram
  session** (Telegram Web login in the bridge: phone + login code) or a
  manual post. A bot-in-group workaround would require the user adding
  `Cloudless_newbot` to Sofia's group — likely not possible (member
  lists are admin-controlled).

**Module map** (course `0322497b`, `md=` ids — saves a re-scrape):

```
welcome  97986fb3340d407f838f83ba4d4711e4   DAY 6   c3f328d185634615ae5ec90bc4e78161
before   6437d4cde09e465ea55028a721492c5f   DAY 7   2086a89a5a8040ab885c2d3e954d95a9
rhythm   c4167d13f3c34baab6dbbb6e4177bf3d   DAY 8   390a746485f64f6884c951538fc8ad77
replays  f8bbdafef3464fe2885ab8601fdaa5da   DAY 9   a88ffeabc9a64435b30a03cf900c7858
DAY 0    642c6d78798249819d9746c8eb23b787   DAY 10  38ace1e002d44f2caa1b1ca403f754f5
DAY 1    09d8ddc89ff44866ac1176166b2e825a   DAY 11  7c99086a69694d7aa22424abff102a4f
DAY 2    17098ecd5a3a4058904e548d9d232e99   DAY 12  f64a30f5b73a40959f0ed2ece2e10275
DAY 3    4853277ef4af456ca516e8fd87aae71a   DAY 13  89ead892f3434570816548cf547bbab2
DAY 4    cca671ce5b574749bed772252fb84239   DAY 14  b3a447579e244c6d9faf062c054f232d
DAY 5    870b714863d44610b6caf4cf7ead3178   DAY 15  97bff638c86e499494af95c123e059c4
                                          DAY 16  e0c68c17c0ab4efc8a3699e059e876f8
                                          DAY 17  e2222267dbf746f4af10e46123e1b2ce
                                          DAY 18  d8221e6d59014981a1cc9709dbc9f871
                                          DAY 19  583c17f1901549ae831b1891aaa6551d
                                          DAY 20  af7244bf9cd34ae2a4d9ed84dd0d72c9
```

**Daily task shape (Week 3+)**: read lesson → write + publish post with
`#sofiakakkavacoach #visibilityerachallenge` → drop link/screenshot in
**Visibility Spotlight** → community action in **Visibility Hub**
(open other members' links, leave a real comment, like).

**Automated watcher** — `app.worker.tasks.skool_watch.watch_skool_vec`
(beat every 3h, default queue): scrapes the classroom module map +
community feed via the bridge (platform tag `skool` — a native SITES
entry, no cookie extraction), seeds silently on first run, then for each
new DAY lesson generates a LinkedIn-personal **draft** through the
brand-voice inference path (`post_blueprint` + creator type apply
automatically) and Slack-alerts. State in Redis (`skool:vec:*`); kill
switch `SET skool:vec:disabled 1`. Browser acquisition is status-gated:
force-preempts only `done`/`idle`/`error` owners, skips the sweep when
an `active`/`waiting`/`extracting` session is mid-work. Drafts carry
`meta_data.needs_media` — attach media before publishing (media rule).


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

## Show / apply the DAY 12 post blueprint
creator_type.py blueprint [apply]

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
- PDFs (source material): `OneDrive/Kakkava Sofia/DAY {1-5} *.pdf`,
  `OneDrive/Kakkava Sofia/3 week/Day{11,12}/*.pdf`
