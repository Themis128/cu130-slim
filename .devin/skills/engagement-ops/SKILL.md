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
| LinkedIn | sidecar → in-session Voyager `/feed/updatesV2` JSON (2026-11-23; DOM scrape dead — obfuscated markup) | post-permalink UI click (voyager reactions endpoint 400s — verified) | `engagement_ingest_li` + `linkedin_actor` |
| X | **402 — paid tier only** | like/retweet/reply OK | needs `like.write` scope re-auth |
| Threads | own-threads only (API limit) | reply OK | `threads_actor` |
| Instagram | ingest only (no text) | likes pending → expect actor failures | `engagement_ingest_ig` + `ig_actor` |
| Facebook | group feed ingest | comments → human gate; likes via sidecar | `engagement_ingest_fb` + `fb_actor` |
| TikTok | tag-feed ingest (textless) | likes only — comment capped off | `engagement_ingest_tt` + `tiktok_actor` |

## Quality gates (2026-11-23 tuning)

- `ENGAGE_MIN_SCORE` (compose env, default 55): SEO floor for inbound target
  posts. Was 80 — rejected ~100% of real posts (casual posts score 50-78).
- Textless posts (video/image-only) skip the SEO gate but are capped at
  `like` — a 3C comment can't be grounded without post text.
- LI ingest filters: sponsored/promoted, company/group containers, own
  posts, post_id dedup, lone-surrogate strip (asyncpg rejects them).
- Stale `scored=true` candidates are never re-evaluated — reset
  `scored=false` for candidates without actions to re-queue under new rules.

## Human gates

- Comments + reposts always `pending_human_gate` — approve before act.
- Sentiment kill-switch (job loss, health, grief) — auto-skip, no override.
- Sidecar `rate_limited` — tasks skip cleanly; never force.

## Ops scripts

- `scripts/engagement-status.sh` — pending/dispatched/acted/failed counts + cooldown state
- `scripts/engagement_sweep.py` — one-shot full engagement pass: fires every platform ingestor (X/LI/IG/TT/FB) + `dispatch_engagements` through Celery on `social-worker-default`, then prints the candidate/action ledger. `--wait N` polls the ledger; `--ingest-only` skips dispatch. Sidecar/browser failures surface as failed/skipped actions — never retried blindly.
- comment-draft approval goes through the content API (`action=approve` on the draft endpoint) — no dedicated script

## Common fixes

- `403 like` → re-auth X (scope list now includes like.write).
- `402 timeline` → X free tier can't read feeds — sidecar path needed.
- LI `no_session` → re-login at localhost:6080/vnc.html.
