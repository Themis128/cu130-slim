# engagement-ops

Operate the autonomous outbound engagement engine (likes/comments/reposts
on connections' posts — the 5x/3x/1x algorithmic-multiplier system).

## Architecture

```
engagement_candidates  ← ingestors (X API · LinkedIn sidecar · Threads)
        ↓ score_candidate()  — freshness decay, connection/CRM weight,
        ↓                     reciprocity tier, sentiment kill-switch
engagement_actions     ← queue_engagement() — jitter 143-412s, daily caps
        ↓ dispatch_engagements (*/15 beat)
        ├─ like            → auto-act via platform actor
        └─ comment/repost  → DMR 3C draft → pending_human_gate
        ↕ update_connection_tiers (daily 06:30) — closes the loop
```

Files: `services/engagement.py`, `models/engagement.py`,
`services/engagement_actors/{x,threads,linkedin}_actor.py`,
`worker/tasks/engagement{,_ingest,_ingest_li,_tiers}.py`.

## Platform reality

| Platform | Feed read | Engage write | Path |
|---|---|---|---|
| LinkedIn | sidecar /feed scrape | voyager via sidecar page | `engagement_ingest_li` + `linkedin_actor` |
| X | **402 — paid tier only** | like/retweet/reply OK | needs `like.write` scope re-auth |
| Threads | own-threads only (API limit) | reply OK | `threads_actor` |
| Instagram | none yet | none yet | needs noVNC login |

## Human gates

- Comments + reposts always `pending_human_gate` — approve before act.
- Sentiment kill-switch (job loss, health, grief) — auto-skip, no override.
- Sidecar `rate_limited` — tasks skip cleanly; never force.

## Ops scripts

- `scripts/engagement-status.sh` — pending/dispatched/acted/failed counts + cooldown state
- `scripts/approve-comments.sh <id>` — approve a human-gated comment draft

## Common fixes

- `403 like` → re-auth X (scope list now includes like.write).
- `402 timeline` → X free tier can't read feeds — sidecar path needed.
- LI `no_session` → re-login at localhost:6080/vnc.html.
