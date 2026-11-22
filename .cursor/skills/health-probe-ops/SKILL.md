# Health-probe ops — tri-state verdicts for account/session checks

Standing contract for every worker task that probes a platform credential
and may flip `social_accounts.status` (or any health flag).

## The rule

A probe returns **three** states, never two:

| Verdict | Meaning | Allowed action |
|---|---|---|
| `True` | Platform definitively answered healthy (2xx) | promote/heal |
| `False` | Platform definitively rejected (400/401/403 auth errors) | may mark expired/revoked |
| `None` | Inconclusive — transport error, 5xx, unexpected status | **leave status untouched** |

`None` covers: DNS failure, connect/timeout/reset (`httpx.HTTPError`),
5xx, and any status outside the definitive sets. Publish lanes filter on
`status='active'` — a false flip silently darkens the channel.

## Incident that created this (2026-11-22)

A worker DNS blip (`Temporary failure in name resolution`) made both
probes in `instagram_session_check` throw → all three IG rows flipped
`expired` + reconnect alerts fired while the `business_login` token was
live (`graph.instagram.com/v26.0/me` → 200 seconds later). IG publishing
was dark until manual remediation. Same bool-only flaw existed in
`token_refresh` (any exception → expired).

Fixes (PR #2505):
- `app/worker/_probe_verdict.py` — `verdict_from_status()` /
  `verdict_from_response()` shared classifier (2xx→True,
  400/401/403→False, else→None; 404 deliberately inconclusive).
- `instagram_session_check` — tri-state probes + `_evaluate()` lane
  combiner + 10s confirmation re-probe before expiring. Its Graph
  probe is finer-grained than the shared helper: `_graph_verdict()`
  parses Meta error codes and only treats OAuthException 190/102 as
  dead (throttling/transient 4xx stay inconclusive); the sidecar
  probe maps 500+LoginRequired to rejected.
- `token_refresh` — `httpx.HTTPError` → error recorded, status kept;
  only definitive rejections expire.
- `account_status_reconcile` — UNDERSTATED drift (probe=active,
  db=expired/revoked) now emits a 24h-deduped Slack alert.

## Checklist for new/changed checkers

1. Probe functions return `bool | None`, not `bool`.
2. `except Exception → None` (log it), never `→ False`.
3. Only 400/401/403-style rejections return `False`.
4. Negative verdicts get a confirmation re-probe or consecutive-failure
   count before flipping persistent state (health-endpoint pattern:
   `unhealthyAfter` consecutive failures, not single probes).
5. Dedup alerts — a flap must not spam Slack/email every sweep.
6. Add a unit test for the transport-error → inconclusive path.

## Audit status (2026-11-22)

| Task | Verdict |
|---|---|
| `instagram_session_check` | fixed — tri-state + recheck |
| `token_refresh` | fixed — HTTPError inconclusive |
| `account_status_reconcile` | probe-only + drift alert added |
| `linkedin_session_check` | already safe (non-401 → inconclusive) |
| `instagram_token_refresh` | already safe (4xx/OAuthException only) |
| `facebook_token_check`, `linkedin_token_check` | probe-only, safe |

## Automated audit

`scripts/probe_audit.py` (mirrored in `.cursor/skills/health-probe-ops/scripts/`)
statically scans `app/worker/tasks/*.py` for the incident class:
`except`-blocks returning `False` or writing dead statuses, dead-status
writes, and `-> bool` probe helpers that can't express inconclusive.

    python3 .devin/skills/health-probe-ops/scripts/probe_audit.py            # worker tasks
    python3 .devin/skills/health-probe-ops/scripts/probe_audit.py --all      # all of app/
    python3 .devin/skills/health-probe-ops/scripts/probe_audit.py --json

First run (2026-11-22): 9 high / 7 review hits. Verified triage:

| Hit | Verdict |
|---|---|
| `token_refresh.py:244` except→expired | false positive — already inside the definitive-rejection branch |
| `instagram_session_check` decrypt-token except→False | acceptable — undecryptable credential IS dead |
| `_check_sidecar_health` except→False | fine — sidecar liveness, not an account verdict |
| `instagram_lane_guard._token_alive` except→False | **fixed** — now `-> bool \| None` via `verdict_from_response`; sweep only counts `is True` as healthy, `is False` as dead (regression tests in test_instagram_lane_guard.py) |
| `instagram_token_refresh.py:64` except→False | review — earlier audit rated the task safe; confirm context |
| `linkedin_session_check.py:156`, `engagement.py:77` dead-status writes | review — confirm guarded by definitive rejection |
