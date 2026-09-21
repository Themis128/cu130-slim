---
name: creator-type-voice
description: >-
  Applies the Sofia Kakkava "Visibility Era" DAY 1 self-assessment (Creator
  Type: Expert / Storyteller / Energizer) and DAY 2 2-Platform Rule to
  cloudless.gr content. Stores the type's post formula and platform tiers in
  the SocialAuto brand voice (brand_voices.voice_signature) so every
  /api/v1/ai/generate-content call — UI generation and all 15 n8n workflows —
  writes in the owner's natural style on the right platforms. Covers the
  quiz, the apply/verify/platforms tool, and the formula text for each type.
  Use when post style feels off-brand, when re-running the assessment, when
  tuning generated post voice, or when deciding which platform a scheduled
  workflow should target.
allowed-tools:
  - read
  - exec
  - grep
  - glob
triggers:
  - user
  - model
---

# Creator Type voice (Visibility Era DAY 1)

## When to use

- Generated posts should match the owner's communication style
- Re-running the DAY 1 self-assessment after a re-positioning
- Switching creator type (expert / storyteller / energizer / blend)

## How it's wired

`POST /api/v1/ai/generate-content` loads `Brand` + `BrandVoice` and injects
`voice_signature` into the system prompt via
`build_brand_system_prompt` (app/services/brand_compliance.py). Three keys
carry the adoption:

- `creator_type` — the type name/blend
- `post_formula` — the numbered structure every post should follow
- `style_notes` — tone guardrails

Every n8n workflow and the UI generator flows through this path — one
`PUT /api/v1/brand/voice` changes all of them.

## DAY 2 — The 2-Platform Rule (adopted)

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

## DAY 3 — Own Your Lane (adopted)

The niche formula — "I help [WHO] who [PROBLEM] so they can [RESULT]" —
stored as `voice_signature.niche` so every generation is audience-anchored:

> I help small teams and small business owners who waste time and money on
> servers and cloud complexity so they can run on simple, cost-efficient
> cloud that just works.

If generated content drifts generic or addresses the wrong audience, check
`niche` first — it is the WHO anchor for every post.

## DAY 4+5 — Profile test + Bio That Sells (adopted)

DAY 4 audit runs via `profile-5sec-test` skill (`scripts/profile_audit.py`).
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

## Tool

```bash
# See the live voice_signature
.devin/skills/creator-type-voice/scripts/creator_type.py show

# Interactive DAY 1 assessment (3 questions → type)
.devin/skills/creator-type-voice/scripts/creator_type.py quiz

# Apply a type to the brand voice (merges, doesn't wipe other keys)
creator_type.py apply expert|storyteller|energizer|blend

# Show the DAY 2 platform tiers + live platform_focus
creator_type.py platforms

# Generate a sample post to check the style
creator_type.py verify [platform]
```

Auth: TOTP login against the admin account inside `social-api` (same flow
as the n8n workflows + socialauto MCP — no secret in the script).

## The three types (DAY 1)

| Type | Builds trust through | Post shape |
|---|---|---|
| **Expert** | Knowledge | Problem → clear steps/framework → action question |
| **Storyteller** | Honesty | Real moment/tension → what happened → honest takeaway → inviting question |
| **Energizer** | Boldness | Bold true claim → one proof point → "do this today" momentum |

The admin's result was a mixed A+B+C → the `blend` profile (Expert-led:
teach concretely, ground in something real, close with energy). `blend` is
the current applied state.

## Guardrails

- `post_formula` forbids **invented anecdotes** — "true observation, client
  lesson, or behind-the-scenes detail" only.
- Keep formulas compact: `voice_signature` is dumped whole into the system
  prompt; long prose wastes tokens.
- If generated content drifts generic, check `creator_type.py verify`
  output and tighten `style_notes` — small DMR models follow short concrete
  rules better than abstract guidance.

## Related

- `socialauto-brand` — brand DNA, tone dimensions, messaging pillars
- `profile-5sec-test` — DAY 4 checklist (profile completeness audit)
- `publish-alert-triage` — alert signature runbook
- PDFs (source material): `OneDrive/Kakkava Sofia/DAY {1-5} *.pdf`
