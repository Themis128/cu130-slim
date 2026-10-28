---
name: social-content-core
description: >-
  Shared content rules every SocialAuto channel skill depends on — the account
  map, the Sofia Kakkava source material, the monetization funnel (FB Stars vs
  Polar), the NLP/SEO quality gate, no-duplicate rule, media pre-flight, and
  the monthly automation-checklist ritual. Read this BEFORE any
  *-content-ops skill.
allowed-tools:
  - read
  - exec
  - grep
  - glob
triggers:
  - user
  - model
---

# Social Content Core

## Data-driven mandate — every decision cites live data

Standing rule: **all skills are data-driven.** Before deciding *what* to post,
*when*, *where*, *which account/media/voice*, or declaring a platform healthy,
query a live surface below and use what it returns. Static heuristics, memory,
and "usually works" are starting points only — never the decision. If a query
fails, say so and surface the gap; do not silently fall back to assumptions.

| Decision | Live surface (query first) |
|----------|---------------------------|
| What performed / what failed | `posts` table — status, `published_at`, `failure_reason`, `scheduled_at`; `publish_queue` |
| Follower truth & deltas | `follower_snapshots` (+ the series guard in `socialauto-analytics`); Nextcloud Tables "SocialAuto Metrics" (synced 3h) |
| Best window / platform pulse | Latest strategy brief JSON (09:00 Athens) — computed from real engagement, see `strategy-brief-triage` |
| Past-day outcomes & lessons | `company_brain.recall()` — nightly journals of real results (Chroma + "Company Brain" Nextcloud Notes) |
| Ad-hoc analytics / SQL | Metabase MCP over Postgres, or `social-postgres` psql |
| Account/token/session health | `/api/v1/accounts`, `scripts/*_tool.py` per-platform CLIs — never assume a token works |
| Monetization progress | `monetization_tool.py report` — measured thresholds, see `monetization-ops` |
| Claim in a post is real | Real numbers from this stack only (digest values, p95s, counts) — invented metrics are forbidden |

Feedback loop: after acting, the outcome lands back in `posts` /
`follower_snapshots` → next journal + strategy brief → next decision. Keep it
closed: check the result of what you shipped, not just that it shipped.

## Account map (verified from Postgres `social_accounts`)

| Platform | Account ID | Handle | Role |
|----------|-----------|--------|------|
| facebook | `9355ed63-7787-43e5-a22d-ae0a33d5176b` | Themistoklis Baltzakis | Personal profile, professional mode, Stars funnel |
| facebook | `ad83c946-0f6b-4fbf-bc63-543d2c2237f5` | cloudless.gr (page ID 116436681562585) | **Canonical brand page** — Messenger bot lives here |
| facebook | `e280d96f-2807-4150-babe-f388d02bcfba` | Cloudless.gr | **DELETED 2026-10-02 — never target** |
| instagram | `38ddbd44-8811-4d0b-be62-a23fd2f50490` | cloudless.gr | Brand |
| linkedin | `18d5cd59-f0c2-4fc4-986e-03601734c7a5` | personal (baltzakis.themis) | Founder voice, finished stories + **The Cloudless Build Log** newsletter (LinkedIn binds newsletters to their author — documented exception to the business-accounts rule; see `linkedin-newsletter-ops`) |
| linkedin | `9c4451bb-e820-489f-8676-76ddbc788ffe` | cloudless-gr | Company Page, corporate voice |
| threads | `1071dcd5-1bc9-4770-923c-d897eb124485` | cloudless.gr | Brand |
| tiktok | `dcda73fb-75b6-47af-b9d1-c1ba404d91a8` | cloudless.gr | Brand |
| twitter | `a89d6852-eff8-479a-835f-50d806cf59dd` | @TBaltzakis | Personal/dev voice |
| telegram | `f75d0132-0964-4236-9766-a6603c997dfd` | Cloudless_newbot | Bot channel |
| whatsapp | `77f17091-3639-4633-b04d-0a3345dd7d3a` | Themistoklis Baltzakis | Business messaging |

Publish-capable but not yet connected: **viber** (`POST /api/v1/viber/connect`,
needs a partner-approved bot token — see `viber-ops`) and **bluesky**
(`POST /api/v1/bluesky/connect`, needs handle + app password — see
`bluesky-ops`). Feed dispatch is wired for both.

Refresh: `docker exec social-postgres psql -U social_user -d social_automation -c "SELECT id, platform, username, status FROM social_accounts ORDER BY platform;"`

## Source material — Sofia Kakkava "Visibility Era"

Always draw from `/mnt/c/Users/tbaltzakis/OneDrive/Kakkava Sofia/` (extract with
`pymupdf` inside `social-api`). Pick the file matching the piece:
DAY 1 (creator type), DAY 2 (2-platform rule), DAY 3 (own your lane),
DAY 4 (5-second profile test), DAY 5 (bio that sells), `2 week/`,
`3 week/` (DAY 11 hooks, DAY 12 post blueprint, DAY 13 content-that-leads +
one-script-four-formats, DAY 14 show-don't-tell storytelling,
DAY 15 show-how-you-think capstone), `Linkedin/`.

Core frameworks: niche formula "We help [WHO] who [PROBLEM] so they can [RESULT]";
"Clarity attracts. Vagueness gets ignored"; plain audience-first language.
Applied voice lives in `brand_voices.voice_signature` — see `content-strategy` skill.

## Monetization funnel (primary optimization goal)

Configured in `brand_voices.voice_signature.monetization_funnel`, propagates to
`/api/v1/ai/generate-content` + all n8n workflows:

1. **Personal funnel → FB Stars**: personal LinkedIn + X soft-funnel to
   `facebook.com/themis.baltzakis` (~1 in 3 posts max) — goal 66→500 followers.
2. **Brand funnel → Polar**: brand accounts close with
   `https://social.cloudless.gr/pricing` or audit `https://cloudless.gr/contact`.

Rules: CTA must be earned by the content; real numbers only; rotate phrasing.
Accounts below monetization thresholds (IG, TikTok, Threads) get product-funnel
CTAs only.

Progress toward each program's gate is measured, not guessed:
`docker exec social-api python3 /app/scripts/monetization_tool.py report`
evaluates every account against its thresholds (Stars 500/30d, Creator
Rewards 10k+100k/30d, X 500+Premium+5M imp, IG ~10k). See `monetization-ops`.

## Trend-driven media → funnel → CRM loop (standing rule)

Every post AND its media must satisfy all three — user instruction
2026-10-18: "all the media posted must be data-driven by the latest trends
with monetization purpose and to lead the followers to my funnels in order
to be on my crm loops":

1. **Trend-grounded**: the topic and the media prompt cite a live trend
   signal picked up *before* drafting — Nextcloud News feeds (Cloudflare
   Blog, DEV.to cloud, HubSpot Marketing, Social Media Examiner, The New
   Stack, Nextcloud — query via News API or the `nextcloud` MCP), the
   latest strategy brief momentum data, or real follower/engagement
   deltas. Name the trend in your reasoning; "evergreen filler" needs an
   explicit justification. Media prompts inherit the trend angle — a
   carousel about a Cloudflare release looks different from a generic
   cloud-cost slide.
2. **Monetization-purposed**: the piece exists to move a funnel metric.
   Before drafting, run `monetization_tool.py report` (or read the latest
   report) and prefer the gate closest to threshold — today that is FB
   Stars personal-funnel growth vs Polar brand conversions.
3. **CRM-loop landing**: every CTA link is UTM-tagged so the follower
   lands on a capture surface that writes into EspoCRM:
   `?utm_source=<platform>&utm_medium=social&utm_campaign=<post-slug>&utm_content=<post-or-account-ref>`
   (paid ads use `utm_medium=paid` — see `linkedin-ads-ops`). Capture
   surfaces already wired: `cloudless.gr/contact` (contact form →
   EspoCRM Lead `lead_source=contact_form` + attribution summary →
   `enrollLeadInAutomation` CRM loop) and
   `social.cloudless.gr/pricing` (Polar checkout). Never ship a bare
   link — untagged traffic is unattributable followers leaking out of
   the loop.
   **Campaign-level slug** (Prakoso et al. 2024 — "integrate social with
   other marketing channels"): when one campaign runs on multiple
   channels (social + email + ads), every channel shares the SAME
   `utm_campaign` — `utm_source`/`utm_medium` distinguish the channel,
   `utm_content` distinguishes the asset. A per-post slug fragments a
   campaign into unattributable pieces; a shared slug lets
   `web_analytics_events` / the datalake join the whole campaign.

## Quality gate — mandatory before publishing

1. Generate via `POST /api/v1/ai/generate-content` (or `/improve-content`) —
   writes in the owner voice on the right platform.
2. NLP plain-English check + SEO score **≥90** at generation (pipeline
   target); **≥80 hard gate at publish** — `PUBLISH_MIN_SEO_SCORE=80`,
   `_enforce_publish_quality` in `worker/tasks/publishing.py` runs the full
   pipeline again right before `publish_to_platform` and **skips + alerts**
   the target when the composite score stays under 80 or gibberish tokens
   survive. Verdict cached per content hash; edit copy → auto re-check.
3. **UI NLP score ≥80** (`POST /api/v1/ai/nlp-check` → `sofia_nlp_score`,
   `app/services/plain_english.py`) — a different scorer from the publish
   composite; the user requires ≥80 before posting. Traps that silently sink
   scores (seen live: single-paragraph DAY posts scored 69):
   - Hook = the FIRST LINE, ≤12 words. A wall-of-text post makes the whole
     caption the hook → −15 `weak_hook`. Always write real line breaks.
   - `—` or `--` anywhere → −8 `em_dash`.
   - ≥4 sentences all in one band (short ≤5 / medium 6-19 / long ≥20) →
     −8 `flat_rhythm`. Mix punches with mediums.
   - No digit or named person → −12 `no_specifics`.
   - No question/CTA signal in the last ~300 chars → −10 `missing_cta`.
   - ≥900 chars without `P.S.` → −7 `missing_ps`.
   - AI-tell phrases → −15 `ai_tell_phrases`.
4. **Lead check (DAY 13)**: the post must move the reader — where they are →
   one clear shift → where they go + one question CTA. If it only informs,
   rewrite. `voice_signature.day13_prompts.check_script` is the review
   prompt; format specs live in `day13_formats`.
5. **LanguageTool `auto_correct` is category-aware**: real-error rules
   (TYPOS/GRAMMAR/CASING/PUNCTUATION/…) auto-apply; style classes
   (STYLE/TYPOGRAPHY/REDUNDANCY — `LANGUAGETOOL_SKIP_CATEGORIES`) are
   *skipped* so intended voice isn't rewritten. Proper nouns, hashtags,
   `urn:li:` mention markup, domains remain protected — never "corrected".
6. Coach attribution: `#sofiakakkavacoach` hashtag only — never her name in prose.
7. **No duplicated posts** — never the same content/story twice, including the
   same piece repackaged in a different format. Different cuts per platform OK.

## Media pre-flight

- Deterministic rules run in `validate_media_for_platform`
  (`app/services/publishing.py::_PLATFORM_MEDIA_RULES`) — wrong dims/format/size
  = skipped, never partial.
- Semantic QA (VLM caption match) via `media-qa-jupyter` skill before any
  generated-media publish.
- IG + TikTok have `required: True` — no text-only posts exist there.

## Publish API

```bash
POST /api/v1/content/posts                 # create draft
POST /api/v1/content/posts/{id}/schedule   # schedule
POST /api/v1/content/posts/{id}/publish-now
POST /api/v1/content/posts/{id}/submit-review  # review workflow
POST /api/v1/content/posts/{id}/cross-post     # fan out to other accounts
```

Failed/odd publishes → `publish-ops` skill (signature table for every
known platform error).

## Monthly ritual — automation checklist

Every month: review services → regenerate `automation-checklist.pdf`
(`scripts/generate_automation_checklist.py` in cloudless.gr) → deploy → announce
on all channels (`monthly-checklist-update` skill).

## Per-channel skills

- `linkedin-content-ops` — personal finished-stories + company page
- `facebook-content-ops` — personal build-log (Stars) + canonical page
- `instagram-content-ops` — media-required brand grid
- `threads-content-ops` — brand text+media
- `x-content-ops` — dev-voice @TBaltzakis
- `tiktok-content-ops` — DIRECT_POST video/photo
- `messaging-channel-ops` — Telegram bot + WhatsApp
