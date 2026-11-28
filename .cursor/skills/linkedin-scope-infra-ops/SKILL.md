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
| Org comment moderation | `app/services/linkedin_community.py` + `linkedin_community` task | beat `linkedin-community-moderation` every 2h |
| Closed-loop won conversions | `linkedin_offline_conversions` task | beat daily 06:40 |
| Org-actor feed engagement | `linkedin_engagement` org pass | `LINKEDIN_ENGAGE_ORG_MAX_REACTIONS_DAY` (3) |
| Ads spend guard | `linkedin_ads_guard` task | beat daily 08:00, `LINKEDIN_ADS_ALLOWED_ACTIVE` |
| Org admin ACL | `app/services/linkedin_admin.py` (`list/set_role/remove_role`) | `rw_organization_admin` |
| LinkedIn Events | `app/services/linkedin_events.py` (`create_org_event`/`post_org_event`/`list_org_events`) | `rw_events` |
| Member DP diff alerts | `linkedin_portability_sync._member_dp` | Redis `linkedin:dma:hash:*` |
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
- `lead_won` (CUSTOM) — `linkedin_offline_conversions` daily beat: EspoCRM
  leads in Converted/Won status that carry a LinkedIn lead-form response
  URN in their description → `stream_conversion` with `conversionValue` +
  currency. Dedup = sha256(email|responseUrn) in `analytics_events`
  (`event_type=linkedin_offline_conversion`). Rule env:
  `LINKEDIN_CONVERSION_RULE_WON`.

## Community moderation (`linkedin_community`)

Scans recent Company Page posts' comments (`r_organization_social_feed`),
classifies spam|lead|question|praise|other. Spam deletes are gated by
`LINKEDIN_COMMENT_HIDE=1` (default dry-run); org's own comments are never
targeted. Lead/question comments → Telegram ops alert. Dedup ledger =
`analytics_events` `event_type=linkedin_community_comment`. Delete uses
`DELETE /rest/socialActions/{postUrn}/comments/{commentId}?actor={orgUrn}`.

## Org admin ACL (`linkedin_admin`)

`GET/PUT/DELETE /rest/organizationAcls` — `list_acls()`,
`set_role(person_urn, role, state)` (state REQUESTED = invite pending,
APPROVED = active), `remove_role()`. Roles: ADMINISTRATOR,
DIRECT_SPONSORED_CONTENT_POSTER, RECRUITING_POSTER,
LEAD_CAPTURE_ADMINISTRATOR, LEAD_GEN_FORMS_MANAGER, ANALYST, CURATOR,
CONTENT_ADMINISTRATOR. CLI: `linkedin_feed_tool.py admins --action
list|set|remove --person urn:li:person:… --role … --state REQUESTED`.

## Events (`linkedin_events`)

`create_org_event(name, starts_at_ms, ends_at_ms, event_url=…)` then
`post_org_event(event_id, commentary=…)` — the Posts API reference is what
publishes the event (draft otherwise). CLI: `linkedin_feed_tool.py events
--action list|create|post`. Registrants route via Lead Sync (EVENT owner
type) into EspoCRM + `lead_created` conversion.

## Member analytics — verified live 2026-11-15

- `memberCreatorPostAnalytics?q=entity` REQUIRES the Rest.li complex key
  `entity=(share:urn%3Ali%3Ashare%3A…)` / `(ugc:…)` — a bare URN 400s.
  Fixed in `analytics_sync._fetch_member_post_analytics`. IMPRESSION does
  NOT support `aggregation=DAILY` (400) — REACTION does.
- `memberFollowersCount?q=me` works → member_followers events (live: 857).
- `/rest/posts?author=urn:li:person:…` 400s "Member permissions must be
  used" — the member token lacks `r_member_social` (read); member post
  URNs come from `post_targets.platform_post_id` instead.
- Org posts must use the ORG token; `linkedin_feed.list_posts` now picks
  member vs org ctx by author URN kind.

## Engagement safety posture

- `LINKEDIN_ENGAGE_AUTHORS` — comma-separated `urn:li:organization:`/`person:` URNs to watch
- `LINKEDIN_ENGAGE_MAX_REACTIONS_DAY` (10), `MAX_COMMENTS_DAY` (3), `MAX_AGE_H` (72)
- `LINKEDIN_ENGAGE_COMMENT_TEMPLATES` — `|`-separated; empty = reactions only
- Dedup + daily-cap ledger = `analytics_events` rows `event_type=linkedin_engagement`
- 401/403 on writes → logged as scope-missing, loop breaks (no hammering)

## Version sunset guard

`linkedin_token_check` (6h beat) also scans every `Linkedin-Version` pin in
`app/`+`scripts/` and alerts ops weekly inside the 90-day runway before each
version's nominal sunset (~12 months after release month, day 15 — e.g.
202601 → 2027-01-15). Versions already past nominal sunset are skipped:
enforcement is endpoint-specific and laxer than docs (the `202312` member-DP
and `202510` demographics pins still serve live traffic). Bump pins to the
latest verified monthly version (currently 202610) when an alert fires.

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
- Member DP granted (app 265100977, `LINKEDIN_MEMBER_DP_TOKEN` in secret store);
  `memberSnapshotData` domains return 404 until LinkedIn populates the export —
  diff alerts fire automatically when data lands
- Lead Sync webhook registration itself is a Campaign Manager UI step
  (Lead Sync → Connect → webhook URL); polling fallback covers the gap
