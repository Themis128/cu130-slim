---
name: trendwatch-ops
description: >-
  Weak-signal trendwatching for SocialAuto content — adopted from Chernenko &
  Babenko, "Development of a data-driven trendwatching model in the European
  business environment: The «Agentic AI» case" (Journal of European Economy
  24(4), 2025). Covers scripts/trendwatch.py — the three-tier model: Google
  Trends RSS signal identification (benchmark US vs target GR/EU), the
  SWOT-based business trend matrix (opportunity / early-window asymmetry /
  noise), and foresight scenario output (leader vs fast-follower) wired to the
  funnel's weakest stage and UTM CTAs. Use when deciding what topic to post,
  validating whether a trend is worth content, or grounding media prompts in a
  live signal per the trend-driven media rule.
---

# Trendwatch-ops

Data-driven trend detection feeding the trend-driven-media rule in
`social-content-core`: before a post or media prompt claims a trend, it should
name a signal this tool (or Nextcloud News / strategy-brief momentum) actually
surfaced.

## Source framework

Chernenko & Babenko's integrated trendwatching model — three cyclical modules:

1. **Signal Identification** — open-source signals, broad coverage (no rigid
   category filter — a hard filter cuts weak signals at inception), benchmark
   vs target-market comparison to detect propagation lag.
2. **Analytical Core** — a business trend matrix mapping quantitative signals
   onto SWOT: growth dynamics → Opportunity, regional asymmetry →
   Weakness/barrier (early-adoption window), volatility → Threat.
3. **Scenario Modelling** — foresight application: innovative value, target
   segment, strategic adaptation (early-adopter "Leader" vs "Fast Follower").

## Tool

`social-automation/backend/scripts/trendwatch.py` — run inside `social-api`:

```bash
docker exec social-api python scripts/trendwatch.py            # report
docker exec social-api python scripts/trendwatch.py --json     # machine-readable
docker exec social-api python scripts/trendwatch.py --slack    # push actionables
docker exec social-api python scripts/trendwatch.py --geos US,GR,GB,DE,FR
```

- **Stage 1**: Google Trends *trending RSS* (`trends.google.com/trending/rss?geo=`)
  per market — default US (benchmark), GR (primary), GB (EU peer). Real-time,
  free, no auth. The interest-over-time API is bot-blocked (429), so dynamics
  come from repeated daily runs, not one-shot history.
- **Stage 2**: each signal scored — `NICHE_LEXICON` relevance (AI/cloud/
  automation/SaaS/marketing…), `NICHE_EXCLUSIONS` strips false positives
  ("social security" ≠ cybersecurity). Classes: `opportunity` (relevant,
  in-market), `early_window` (relevant, benchmark-only → propagation lag),
  `noise` (non-niche traffic — discarded).
- **Stage 3**: actionable signals get strategy (leader → publish inside the
  24-48h window; fast-follower → pre-draft for the EU/GR lag wave), channel
  picked from the live funnel-weakest transition, and a canonical UTM CTA
  (`utm_campaign=trend-<slug>`).

Exit 0 clean, 2 if any geo feed failed (errors printed to stderr — exposed,
never silently skipped).

## Using the output

- `LEADER` rows: valid trend citations for today's post — feed the query +
  news hook into `/api/v1/ai/generate-content` and the media prompt.
- `FAST-FOLLOW` rows: benchmark-market trends not yet here — queue for the lag
  window (hours–days per the paper's US→EU diffusion finding); don't publish
  as if it's a local trend.
- No actionable rows → the honest answer is "no qualifying trend today"; fall
  back to strategy-brief momentum or Evergreen pillars — never fabricate one.

## Honest limitations

- Same-day snapshots only (RSS gives ~10 trending queries per geo, not the
  paper's 12-month series) — longitudinal confidence needs daily cadence.
- Relevance is lexicon-based; borderline niche calls are judgment — a
  `LEADER` flag is a warrant to *draft*, not to auto-publish.
- No persistence: signals are ephemeral by design; `--slack` or the report is
  the record. If a signal should be tracked, that's strategy-brief territory.
