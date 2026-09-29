# Cross-Promo Drip

Schedule a multi-platform cross-promotion drip campaign through SocialAuto —
e.g. "follow me on Facebook" posts across LinkedIn / X / Threads / Instagram to
grow a profile toward a platform gate (Meta Stars 500-follower threshold,
monetization, etc.).

## When to use

- Driving followers from existing platforms to a target profile
- Any multi-platform campaign where each copy must be platform-native and
  quality-gated (SEO ≥90, spellcheck clean, verbatim storage)
- Scheduling a drip over days/weeks rather than a same-day blast

## Campaign rules (web-researched, verified Oct 2026)

1. **Platform-native copy, never identical blasts.** Same angle, different
   voice per platform. Same-day identical "follow me" posts read as spam.
2. **Drip over days.** 1–2 posts per platform, ≥7 days between posts on
   the *same* platform, and the campaign spans ≥2 weeks overall. (The
   spacing rule is per-platform: two LinkedIn posts 8 days apart inside a
   two-week campaign is correct.)
3. **LinkedIn: keep the link INLINE in the post body.** The old "link in
   first comment" trick now costs ~80% reach + 3.4× worse conversion
   (van der Blom 2025). For personal profiles the inline-link penalty is
   near zero — the real tax is the *preview card*, not the URL string.
4. **Instagram requires media** and captions have no clickable links — the
   URL sits inline as plain text. Pick an image from the media library whose
   `generation_prompt` matches the copy theme (`GET /api/v1/media/assets`).
5. **No link_url field for cross-promo** — keep the target URL inside
   `content_text` (and for IG it's stripped anyway).
6. **Quality gate:** every copy must hit SEO ≥90 via
   `POST /api/v1/ai/analyze-content` `{content, platform}` with zero
   spellcheck issues. Known false-positive: URLs and `#hashtags` get flagged
   as `long_uncommon_words` — ignore those, they must stay.
7. **Verify after create**: stored `content_text` must equal the draft
   verbatim (publish-time `auto_correct` is advisory but the stored text is
   what ships), `status=scheduled`, every target `pending`. `drip.py`
   deletes any created post that fails this check.
8. **Last-tier guard (2-Platform Rule)**: Twitter/X and TikTok are
   "opportunistic only" — never add a *schedule* that fires into them
   (see `creator-type-voice` SKILL.md). Cross-promo on those platforms is
   manual/publish-now only. `drip.py --schedule` rejects last-tier targets.

**Fail-closed gate**: a post is created only when the analyzer returns a
numeric SEO ≥90 AND a successful empty spellcheck. If either service is
down (null response), the post is skipped — an outage must not become a
backdoor for unchecked copy. Re-running `--schedule` is idempotent:
keys already in `<plan>.created.json` are skipped **only if the plan
entry is unchanged** — a fingerprint of text/time/accounts/media detects
edits and fails loudly rather than silently keeping a stale schedule.
Text already scheduled to the same platform is adopted (its real post ID
is recorded), never duplicated. Instagram entries without `media_ids`
are rejected.

## Best posting times (Athens, EEST) — Buffer/Later 2026 studies

| Platform  | Best slots                          |
|-----------|-------------------------------------|
| LinkedIn  | Wed 16:00, Fri 15:00–16:00, Tue–Wed 07:45–08:30 |
| X/Twitter | Tue–Thu 08:00–10:00 (peak: Tue 09:00) |
| Threads   | Wed/Thu 09:00, weekday mornings     |
| Instagram | Thu 09:00, Wed 12:00 or 18:00       |
| Facebook  | Weekday 08:00–12:00                 |

Convert to UTC for `scheduled_at` (EEST = UTC+3 in summer, EET = UTC+2 winter).

## Tool: scripts/drip.py

```bash
cd /home/tbaltzakis/cu130-slim

# 1. Analyze only — prints SEO/spell/PE per copy
python3 .devin/skills/cross-promo-drip/scripts/drip.py plan.json

# 2. Create scheduled posts + verify verbatim/targets inline
python3 .devin/skills/cross-promo-drip/scripts/drip.py plan.json --schedule

# 3. Re-verify later — auto-loads post IDs from plan.created.json
python3 .devin/skills/cross-promo-drip/scripts/drip.py plan.json --verify
```

`drip.py` refuses to schedule any copy scoring <90 — fix and re-run.
Post IDs are written to `plan.created.json`.

plan.json format — see `scripts/drip.py` docstring. Required per post:
`key`, `platform`, `text`, `account_ids`, `scheduled_at`; optional `media_ids`.

## Account IDs (stable)

- LinkedIn personal: `18d5cd59-f0c2-4fc4-986e-03601734c7a5`
- Twitter/X: `a89d6852-eff8-479a-835f-50d806cf59dd`
- Threads: `1071dcd5-1bc9-4770-923c-d897eb124485`
- Instagram: `38ddbd44-8811-4d0b-be62-a23fd2f50490`
- (Re-resolve with `GET /api/v1/accounts` if these 404.)

## Reference campaign: Facebook profile push (Oct 2026)

Goal: grow `facebook.com/themis.baltzakis` from 1 → 500 followers for Meta
Stars eligibility (500 followers + held 30 consecutive days — no API/config
can bypass; organic growth is the only path).

Scheduled Oct 1–15, 2026 (4 posts; the two X posts were dropped —
X is last-tier, opportunistic only per the 2-Platform Rule):

| Key | Platform | Athens | Angle |
|-----|----------|--------|-------|
| LI-1 | LinkedIn | Thu Oct 1 16:00 | "Moving the unpolished build log to Facebook" |
| TH-1 | Threads | Thu Oct 8 09:00 | Casual version |
| LI-2 | LinkedIn | Fri Oct 9 15:30 | "Exactly 1 follower, not a typo" |
| IG-1 | Instagram | Thu Oct 15 09:00 | Pi-boards image + caption |

Post IDs in the `.created.json` / Slack digest when they fire. Never
re-create duplicates — check `GET /api/v1/content/posts?status=scheduled`
before scheduling another round.
