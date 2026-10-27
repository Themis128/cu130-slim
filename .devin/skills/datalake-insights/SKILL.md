# datalake-insights — lake read-back → actions

`datalake_export` pushes snapshots to R2; this is the **consumer** side.

- `app/services/datalake_insights.py` — `compute_survey_signals`/`pick_actions`
  (pure) + `build_signals`/`apply_signals` (async DB boundary). Survey pain
  words → content topics, price anchors → retainer band, audit-yes → Slack
  escalation, all persisted as `AnalyticsEvent(type="datalake_signals")` so the
  next export carries the signal into the gold layer.
- `app/worker/tasks/datalake_insights.py` — `datalake_insights_sweep` beat,
  every 6h at :20 (10 min after the export), default queue, per-team.
- Slack ping only when there's an actionable line (never an empty digest).
- Tests: `tests/unit/test_datalake_insights.py` (7).
