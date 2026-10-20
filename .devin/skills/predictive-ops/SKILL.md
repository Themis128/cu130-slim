---
name: predictive-ops
description: >-
  Predictive-analytics readiness audit for SocialAuto — adopted from
  Adesina, Iyelolu & Paul, "Leveraging predictive analytics for strategic
  decision-making" (World Journal of Advanced Research and Reviews 22(3),
  2024, DOI 10.30574/wjarr.2024.22.3.1961). Covers
  scripts/predictive_audit.py — the paper's chain (quality data → model →
  validated prediction → decision) applied to our live predictive
  surfaces: insights-engine best-hour learning sufficiency, follower
  growth velocity → monetization threshold ETAs, momentum-anomaly
  detection, and history coverage. Use when asking "can we trust the
  best-window/ETA predictions", checking forecast inputs, or deciding
  whether a threshold goal is reachable at current pace.
---

# Predictive-ops — Adesina et al. data→prediction chain on live data

Source: Abayomi Abraham Adesina, Toluwalase Vanessa Iyelolu, Patience
Okpeke Paul — *Leveraging predictive analytics for strategic
decision-making: Enhancing business performance through data-driven
insights* (World Journal of Advanced Research and Reviews, vol. 22 no. 3,
2024, pp. 1927–1934, DOI 10.30574/wjarr.2024.22.3.1961). PDF:
`OneDrive/Kakkava Sofia/Papers/ff29715b465db49a3f2f8b30222f2bea02a8.pdf`.

## The paper's model

- **Components**: data mining + ML + statistics turn history into
  predictions that inform strategic decisions.
- **The pipeline**: data management/governance (quality first — *no model
  compensates for bad data*) → analytical techniques (regression,
  classification, clustering) → validated, monitored models →
  visualized insights decision-makers act on.
- **Business impact**: profitability, efficiency, market share — via
  anticipating trends rather than reacting.
- **Risks**: privacy/ethics, algorithmic bias, decisions on incomplete
  data or intuition when analytics are absent.

## Our predictive surfaces → what the audit checks

| Paper stage | SocialAuto surface | Live check |
|---|---|---|
| data quality | snapshot history coverage | distinct days in `follower_snapshots` / `post_analytics_snapshots` |
| model training | shrunk-bucket best-hour learning (`insights_engine`, k=4 shrinkage → `best_hour_athens` → `learned_hours` in monetization scheduling) | observations per platform in 60d ≥ 12 — below that, the "learned" hour is just the static heuristic |
| prediction output | threshold ETAs (`days_to_goal`, FB Stars 500) | 14d follower velocity per account; stalled = no ETA, honest "not growing" |
| anomaly guard | corrupted series must not feed predictions | 7d delta >50% of base AND >50 abs → flagged (follower-analytics-guard methodology) |

## The tool

```bash
docker exec social-api python scripts/predictive_audit.py          # human report
docker exec social-api python scripts/predictive_audit.py --json   # digests
```

- Per-platform training volume behind `best_hour_athens` — below
  `_MIN_HOURLY_OBS` (12) the estimate shrinks to the static heuristic
  table, i.e. the "prediction" is a baseline, not learned.
- Per-account 14d follower velocity + days-to-goal ETA (facebook → 500).
- Momentum anomalies (corrupted series) and history coverage.
- **Gaps → exit 1**: insufficient training data, stalled growth,
  anomalous series, <30d history.

## First run (2026-10-20)

```
history: 34d followers, 37d analytics — thin but usable
all 6 platforms: sufficient best-hour training volume
velocity: linkedin/@baltzakis.themis +0.57/day, linkedin/cloudless-gr
  +1.29/day; all other accounts stalled (~0/day)
fb/@Themistoklis 66 followers, +0.00/day; fb/@cloudless.gr 2, +0.07/day
  → ~7114d to Stars goal (effectively unreachable at this pace)
gaps: 5 stalled accounts
```

Honest readings:

- **The learning is trained but the growth story is flat** — best-hour
  estimates are data-backed; the monetization ETAs are the weak link
  (only LinkedIn accounts are actually growing).
- **FB Stars is a pacing problem, not a tracking problem** — +0.07/day
  on the page means ~19 years to 500. The funnel needs the
  profile/content strategy to change (facebook-content-ops), not better
  forecasts.
- **Stalled ≠ broken** — several accounts sit at tiny bases (twitter 0,
  threads 1); stalled flags are honest "no momentum", not corruption.

## Dispatch rules

| Gap | Likely cause → skill |
|---|---|
| insufficient training obs | platform barely posts/analyzes → `*-content-ops`, check analytics_sync |
| stalled growth | content/strategy not converting followers → `social-content-core`, `monetization-ops` |
| anomalous series | corrupted snapshot interleave → `follower-analytics-guard` cleanup SQL |
| thin history | stack is young — re-run as history accrues; don't trust long-horizon ETAs yet |

## Relationships

- `follower-analytics-guard` repairs the corrupted series this audit
  *excludes* from prediction — guard first, then predict.
- `monetization-ops` reports thresholds/eligibility; this audits whether
  the *forecast* inputs behind its ETAs are sane.
- `strategy-brief-triage` sanity-checks brief outputs; this checks the
  *training data* underneath the learned best-window and momentum
  signals the brief consumes.
