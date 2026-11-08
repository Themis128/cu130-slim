# LinkedIn Expansion Plan (2026-11-08)

Goal: close every LinkedIn gap + add relevance-based engagement automation
+ AI newsletter creation/posting fed by live trend signals.

## Existing surface (verified)
- Publish/comment/analytics: app/api/linkedin.py
- DM bot: app/worker/tasks/linkedin_messenger.py (messenger queue)
- Sessions: check 12h + refresh beats; invites daily 10:30; ads report 10:00;
  engagement score; lead routing; boost-winner; ad-library watch Mon 9:00
- Ads: linkedin_ads* services + sync/control/report tasks
- Accounts live: personal (token→2027-01-06), cloudless-gr org (→2027-01-03)

## Work items

### A. Relevance-based engagement automation (NEW — highest priority)
1. `app/services/linkedin_engagement.py` — scorer: embed post text +
   brand_voices voice_signature + ICP keywords → cosine sim; threshold gate.
2. `app/worker/tasks/linkedin_engage.py` — beat task (e.g. 3x/day):
   - feed scan: own network posts via sidecar `/feed` or org social API
   - relevance filter → like/comment (comment via linkedin.generate-comment
     AI endpoint with voice constraints)
   - group scan: joined-groups posts (sidecar) → same scorer → engage
   - rate limits: max N engagements/account/day, jittered pacing,
     dedup table `linkedin_engagements(post_urn, action, day)`
3. Guardrails: never engage own posts; skip politics/controversy keywords;
   human-approval mode flag per account (default: comments need approval,
   likes auto).

### B. Newsletter creation + posting
1. `app/services/linkedin_newsletter.py` — draft pipeline:
   - inputs: trend_signals (TikTok CC + Google Trends via trend_ingest),
     top-performing own posts (engagement_score), espocrm topics
   - outline → sections → voice-constrained draft (reuse linkedin_ai)
2. Publish path: LinkedIn newsletter API is creator-article only —
   check `linkedin_newsletter_bridge.py` (exists, 471 lines) for current
   state; likely needs newsletter_id creation via UI once, then API posts.
3. Beat: weekly draft → Slack approve → publish (mirror offer-survey flow).

### C. Gap closures
1. Org follower snapshots: extend follower_snapshots worker to org account.
2. linkedin_newsletter_bridge auto-publish on blog publish webhook.
3. Org social webhooks → unified inbox (linkedin_events.py is thin).
4. Lead-gen form sync: verify Marketing Developer Platform approval;
   fallback = lead_boost task already covers.
5. Profile field updates via linkedin-sidecar (bio/slogan/cover).
6. Ad Library intel → trend_context / content ideas feed.

## Order
A (engagement) → B (newsletter) → C items by value. Each ships as its own
PR to master with unit tests + ruff clean.
