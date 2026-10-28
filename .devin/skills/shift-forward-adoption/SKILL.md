---
name: shift-forward-adoption
description: >
  Adopted patterns from Cisco DevNet Shift.Forward() (AX Pass post-event
  recap, ingested 2026-10-28). Maps the event's agentic-AI findings onto
  this stack — each has a concrete adopt surface already in the repo.
  Source: Downloads/shift-forward-recap/{private-recap,collective-intelligence}.md.
---

# Shift.Forward → Stack Adoption Map

## 1. Cognitive observability (SCOPE → our task telemetry)
Event: SCOPE detects "silent failures" — tasks pass but logic's wrong.
Adopt: the worker already logs task results per-beat; the gap is
*semantic* success checking (a task that returns `{'sent':0}` vs the
task that returned a plausible-looking wrong payload). Adopt: extend
`celery_probe.py` with an outcome-check per task — assert the result
shape, not just `succeeded`.

## 2. ACCORD/CONCORD → shared context across skills
Event: agents fail from differing opinions + no shared intent; protocols
align intent *before* work + coordinate during execution.
Adopt: our skills each have SKILL.md context but no shared-intent layer —
`company_brain.recall()` journals already exist per-session; adopt by
writing a shared `context.json` (goal/scope/decisions) that every beat
task + skill reads, so survey+social+datalake agents act on the same
intent.

## 3. Loop engineering → task harnesses
Event: wrap non-deterministic LLM calls in structured control loops —
single-responsibility + retry-with-context + context-window management.
Adopt: our content/spellcheck/decision paths call LLMs loosely; adopt a
`loop_harness` wrapper (single-responsibility prompts + bounded retry +
context trim on repeat) — maps to `app/services/ai*.py` callers.

## 4. CAIPE / n8n syslog→vector→LLM pipeline → our Loki+DMR
Event: N8N ingests syslog → vector DB → local LLM queries → interactive
troubleshooting. We already have Loki + Grafana + DMR on omv — the
pattern is a near-drop-in: n8n workflow that pulls Loki logs → embeds →
vectorize/D1 → a DMR-query endpoint. Pilot = analytics-stack triage
already has this shape.

## 5. Governance + human gates from inception
Event: multi-agent systems need governance/human-oversight hardcoded
(role-gated data, agent identity, deterministic tool access).
Adopt: already implemented as `post_autoapprove`/`survey_autosend` +
human-gate-bridges — the pattern's validated; extend per the
'agent passport' idea: every beat task carries an actor tag (`autopilot`
vs `user`) — post approval already marks `autopilot` in PostComment.
