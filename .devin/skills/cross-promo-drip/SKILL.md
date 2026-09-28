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
2. **Drip over days.** 1–2 posts per platform spread across ≥2 weeks.
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
   what ships), `status=scheduled`, every target `pending`.

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

# 3. Re-verify later (add "post_id" to each post in plan.json,
#    or point at the .created.json it writes)
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

Scheduled Oct 1–16, 2026 (all fired as `scheduled`, targets `pending`):

| Key | Platform | Athens | Angle |
|-----|----------|--------|-------|
| LI-1 | LinkedIn | Wed Oct 1 16:00 | "Moving the unpolished build log to Facebook" |
| TW-1 | X | Thu Oct 2 09:00 | Short-form variant |
| TH-1 | Threads | Wed Oct 8 09:00 | Casual version |
| LI-2 | LinkedIn | Fri Oct 10 15:30 | "Exactly 1 follower, not a typo" |
| TW-2 | X | Tue Oct 14 09:00 | Second touch, different hook |
| IG-1 | Instagram | Thu Oct 16 09:00 | Pi-boards image + caption |

Post IDs in the `.created.json` / Slack digest when they fire. Never
re-create duplicates — check `GET /api/v1/content/posts?status=scheduled`
before scheduling another round.
