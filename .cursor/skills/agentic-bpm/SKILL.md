---
name: agentic-bpm
description: >-
  Agentic Business Process Management governance for SocialAuto's agent fleet —
  adopted from Vu, Klievtsova, Leopold, Rinderle-Ma & Kampik, "Agentic BPM:
  Practitioner Perspectives on Agent Governance in Business Processes" (BPM
  2025, arXiv:2504.03693). Covers the six adoption recommendations mapped onto
  SocialAuto, the per-worker governance contract in scripts/workforce_audit.py
  (goal/guardrails/autonomy/fallback — ungoverned workers fail the audit), and
  the human-escalation Slack lane. Use when adding a new agent/worker, deciding
  how much autonomy to grant, wiring a fallback, or answering "is the agent
  fleet governed".
---

# Agentic BPM — governing the SocialAuto agent fleet

Source: Vu, Klievtsova, Leopold, Rinderle-Ma, Kampik — *Agentic Business
Process Management: Practitioner Perspectives on Agent Governance in Business
Processes*, Responsible BPM 2025 ([arXiv:2504.03693](https://arxiv.org/abs/2504.03693)).
Qualitative study of 22 BPM practitioners; distilled six recommendations for
responsible agent adoption plus four BPM-alignment actions. Adopted
2026-10-19 — full text is open access on arXiv.

Companion skill: `agent-workforce` (liveness — "did the worker clock in").
This skill is the **governance layer** — "is each worker bounded, guarded,
and recoverable".

## The six recommendations → where SocialAuto implements them

| Paper recommendation | SocialAuto implementation |
|---|---|
| **Business context** — define goals, benefits, costs | `Worker.goal` in `scripts/workforce_audit.py` — one business outcome per worker, audited live |
| **Guardrails** — legal/ethical/org boundaries | `Worker.guardrails` + the deterministic layers already in code (media-required, phash dedup, NLP ≥80 gate, `_plausible_follower_count`, bot disclosure) — see `bot-guardrails` for conversational guards |
| **Human-agent collaboration** — clear roles, intervention points | `Worker.autonomy` tier (`auto` / `review` / `supervised`) + escalation queries that park items for a human (stale locks, dead sessions, failed targets) |
| **Customization** — autonomy level, oversight, documentation | The `Worker` dataclass IS the per-agent config; a worker without a complete contract reports `ungoverned` |
| **Risk management** — monitor, prevent undesired adaptability | `workforce_audit.py` live checks (presence/output/escalation); agents are deterministic Celery beats, not self-modifying loops — "no undesired evolution" holds by construction |
| **Adoption** — safe integration + fallback | `Worker.fallback` — every worker declares its safe-reversion path (retry → park → human). Publishing has real fallback chains (IG 5-path, X API→browser) |

## The four BPM-alignment actions → our answers

| Paper action | Our position |
|---|---|
| Human-agent balance | Agents do deterministic sweeps + drafting; humans own escalation decisions and `review`-tier approvals |
| Human involvement | Humans are **supervisors with selective intervention** — Slack alert → human acts; agents never unblock themselves past their escalation lane |
| Process structures | Demand-driven beats (idle ≠ broken) instead of rigid periodic runs — matches the paper's "dynamic structures" |
| Performance metrics | `evidence` queries (real artifacts within 2× cadence) + `socialauto_*` Prometheus gauges per platform/account — outcomes, not process state |

## The governance contract (audited live)

Every `Worker` in `scripts/workforce_audit.py` must declare:

```python
goal="one business outcome it owns",
guardrails=("hard boundary 1", "hard boundary 2"),
autonomy="auto" | "review" | "supervised",
fallback="what happens when it fails",
```

- Missing any field → status `ungoverned`, exit code 1, named in `out["ungoverned"]`.
- `--governance` prints the contract table; `--slack` posts the attention
  list (the paper's human-intervention lane) via `post_alert_to_slack`.

```bash
docker exec social-api python scripts/workforce_audit.py --governance
docker exec social-api python scripts/workforce_audit.py --slack
```

## Adding a new agent/worker

1. Add the Celery beat with a bounded domain and cadence.
2. Register it in `WORKFORCE` with the **full governance contract** — if you
   cannot name the goal, the guardrails, and the fallback, the worker is not
   ready to ship.
3. Give it observable output (a domain-table artifact within 2× cadence) —
   if it leaves no artifact, add one to the code first.
4. Give it an escalation lane (a `SELECT count(*) …` for parked items).
5. Run the audit — `ungoverned` blocks a clean run.

## Honest gaps (do not paper over)

- `autonomy` is today **declared, not enforced** — the tier is documentation
  for humans, not a code-level gate, except `review` which maps to the real
  NLP/quality gate for content-factory.
- Guardrails are enforced where they already exist in code (dedup, media
  gate, cooldowns). A declared guardrail with no enforcing code is a
  promise, not a control — prefer citing an existing mechanism.
- There is no `/api/v1/ops/agent-audit` endpoint yet — the audit is CLI
  only; wire it into the ops router if the UI needs it.
