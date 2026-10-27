# Growth Funnel Ops

Data-driven gate selection + funnel posting for SocialAuto monetization.
Read `social-content-core` first — this skill is its funnel executor.

## Pick the gate (always first)

```bash
docker compose exec -T social-api python scripts/monetization_tool.py report
```

Nearest-gate rule (verified 2026-10-27): **FB Stars personal 66→500** is the
only sub-threshold numeric gate — IG/TikTok/Threads all need 10k. The funnel
that feeds it: **LinkedIn personal + X soft-CTA to `facebook.com/themis.baltzakis`,
~1 in 3 posts** (brand accounts → Polar `social.cloudless.gr/pricing` /
audit `cloudless.gr/contact`).

## Tools

`scripts/queue_funnel_post.py` — full quality pipeline in one call:
`generate-content` → `nlp-check` (hard gate ≥80 per UI rule) →
`tracked_url` UTM (source=platform, medium=organic_social, shared campaign
slug — never bare links) → `POST /api/v1/content/posts` → `schedule`.

```bash
docker compose exec -T social-api python /app/scripts/../skills/queue_funnel_post.py \
  --funnel personal --platform linkedin --topic "real build-log claim" \
  --cta https://facebook.com/themis.baltzakis --at 20:30
```

`--funnel personal` = Stars soft-funnel (LinkedIn/X only, ≤1-in-3 cadence —
the script checks last-20 posts for cadence and refuses to oversaturate).
`--funnel brand` = Polar/audit CTA for brand accounts.

## Cadence check (data-driven saturation)

Script queries `posts` for the last 20 published with `facebook.com/themis`
in `content_text` → if ratio ≥1/3 it skips with a reason. Never
hand-override — the 1/3 cap is the funnel spec.

## Reels lever (IG gate)

`branded-video-pipeline` makes ~40s PIL+ffmpeg clips without ComfyUI — use it
for the IG volume the gate needs; video drove every engagement spike.

## Open code-level items (not skill actions)

- FB `insights` HTTP 400 — deprecated field on personal profile (fix in `meta-graph-ops` surface).
- DMR contention — `gpu-arbiter` slot reservation; CF fallback mitigates.
