---
name: linkedin-scope-infra-ops
description: Operate the LinkedIn scope-infra lanes built on app 264925843 — Community Management feed engagement (reactions/comments), Conversions API rules + server-side event ingest, Lead Sync webhook, and member/org analytics collectors. Use for LinkedIn engagement, conversion tracking, lead-gen routing, or "did the LinkedIn scopes actually do something" questions.
---

# LinkedIn Scope Infra Ops

Everything below is live on the granted scope set (verified 2026-11-15 on app
`264925843` / client `77j8kzfi1a6jn8`). All services self-gate when
`LINKEDIN_ADVANCED` / `LINKEDIN_FEED_WRITES` are off.

## Surfaces

| Lane | Code | Gate |
|---|---|---|
| Feed reads (posts, metadata, comments) | `app/services/linkedin_feed.py` | token only |
| Feed writes (react/comment/delete) | same — `react()`, `comment()` | `LINKEDIN_FEED_WRITES=1` |
| Watchlist engagement worker | `app/worker/tasks/linkedin_engagement.py` | beat `linkedin-engage` */45m |
| Conversions API send | `linkedin_advanced.stream_conversion()` | `rw_conversions` + rule URN |
| Rule lifecycle | `linkedin_advanced.list/create_conversion_rule`, `resolve_conversion_rule` | — |
| Rule auto-ensure | `linkedin_conversion_ensure` task, beat `linkedin-conversion-ensure` daily 05:10 | `LINKEDIN_ADVANCED=1` |
| Generic ingest | `POST /api/v1/webhooks/linkedin-conversion` | `X-Conversion-Key` = `LINKEDIN_CONVERSION_INGEST_KEY` |
| Lead Sync webhook | `GET+POST /api/v1/webhooks/linkedin-lead-sync` | LinkedIn `X-LI-Signature` HMAC (client secret) |
| Lead subscription mgmt | `linkedin_advanced.create/list_lead_notification` | `r_marketing_leadgen_automation` |
| Lead form question map | `linkedin_advanced.get_lead_form` + `lead_form_question_map` | resolves questionId→email/name |
| Lead routing (shared) | `linkedin_lead_boost.route_lead_response()` | `LINKEDIN_ADVANCED=1` |
| Ops endpoints | `/api/v1/linkedin/feed/{posts,metadata,comments,react,comment}` | admin JWT |
| CLI | `backend/scripts/linkedin_feed_tool.py` | run in backend container |

### Lead Sync webhook contract (verified vs Microsoft Learn 2026-11-15)

- `GET ?challengeCode=…` → `{challengeCode, challengeResponse}` where
  response = hex HMAC-SHA256(challengeCode, LINKEDIN_CLIENT_SECRET). Re-validated
  ~every 2h; 3 consecutive failures → endpoint blocked.
- `POST` body `{type:"LEAD_ACTION", leadGenFormResponse, owner, leadGenForm,
  leadType, leadAction, occurredAt}`; verify `X-LI-Signature` =
  hex HMAC-SHA256(`"hmacsha256="` + raw body, client secret), constant-time.
- Dedup key = `leadGenFormResponse:occurredAt` (response URN is REUSED across
  resubmissions). `leadAction=DELETED` → ack, don't route.
- Full response via `GET /rest/leadFormResponses?ids=List("…")&owner=(…)
  &leadType=(leadType:…)` → `results[id]` (no `/leadFormResponses/{id}` path).
- Subscribe via `POST /rest/leadNotifications` `{webhook, owner:{sponsoredAccount|organization}, leadType}`;
  CLI: `linkedin_feed_tool.py register-lead-sync --webhook https://social.cloudless.gr/api/v1/webhooks/linkedin-lead-sync --owner urn:li:sponsoredAccount:512642510`
  (**run only after the API deploy is live** — LinkedIn validates the URL at
  subscription time; a 404 endpoint fails validation).

## Conversion events

`linkedin_advanced.CONVERSION_EVENTS` maps event → (env/secret key, LI `type`):

- `audit_booked` (LEAD) — Cal.com webhook already streams this
- `lead_created` (LEAD) — Lead Sync webhook + poller
- `contact_created`, `consultation_booked` (LEAD) — cloudless.gr site fires
- `signup` (SIGNUP), `purchase` (PURCHASE) — cloudless.gr site fires

Rule URNs resolve env → `social_secrets` (same key name). Missing rules are
auto-created daily by `ensure_linkedin_conversion_rules` and persisted back.

## Engagement safety posture

- `LINKEDIN_ENGAGE_AUTHORS` — comma-separated `urn:li:organization:`/`person:` URNs to watch
- `LINKEDIN_ENGAGE_MAX_REACTIONS_DAY` (10), `MAX_COMMENTS_DAY` (3), `MAX_AGE_H` (72)
- `LINKEDIN_ENGAGE_COMMENT_TEMPLATES` — `|`-separated; empty = reactions only
- Dedup + daily-cap ledger = `analytics_events` rows `event_type=linkedin_engagement`
- 401/403 on writes → logged as scope-missing, loop breaks (no hammering)

## Verify

```bash
docker exec social-api python scripts/linkedin_feed_tool.py posts \
  --author urn:li:organization:<orgId> --count 3
docker exec social-api python scripts/linkedin_feed_tool.py rules
docker exec social-api python scripts/linkedin_feed_tool.py ensure-rules
# dry-run a write (LINKEDIN_FEED_WRITES unset):
docker exec social-api python scripts/linkedin_feed_tool.py react \
  --actor member --root urn:li:share:<id> --type LIKE
```

Check beat ran: `analytics_events` where `event_type='linkedin_engagement'`,
or worker logs `linkedin-engage:` stats line.

## cloudless.gr side

`src/lib/linkedin-server-conversion.ts` fires `contact_created` / `signup` /
`purchase` to the ingest endpoint. Set wrangler secret
`SOCIALAUTO_CONVERSION_KEY` = SocialAuto `LINKEDIN_CONVERSION_INGEST_KEY`
(both unset = open ingest, still works).

## Not covered (external gates)

- `rw_dmp_segments` Matched Audiences — `linkedin_dmp_sync` staged, product grant pending
- `r_dma_portability_self_serve` Member DP — portal watcher driving
- Lead Sync webhook registration itself is a Campaign Manager UI step
  (Lead Sync → Connect → webhook URL); polling fallback covers the gap
