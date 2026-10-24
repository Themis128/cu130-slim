# Backlog — leftover work across sessions

Living list. **Every agent session must read this at start and update it at end** —
check off what landed, add anything left unfinished, and keep items verbatim enough
to act on (IDs, URLs, file paths). Rules: `.devin/rules/` + `AGENTS.md`.

## Registered leftovers — 2026-10-21

All entries below remain open until their acceptance criteria are verified.
Issue registration is not resolution. No outreach, test publishing, paid ads,
verification-code requests or destructive draft cleanup was performed.
Historical observations below retain their dates; re-query before acting.

| Issue | Remaining work | Gate |
| --- | --- | --- |
| ~~[#782](https://github.com/Themis128/cu130-slim/issues/782)~~ | Secondary Meta recovery email — CLOSED 2026-10-22: `tbaltzakis@cloudless.gr` verified present in Accounts Center | resolved |
| ~~[#783](https://github.com/Themis128/cu130-slim/issues/783)~~ | IG lock/restriction — CLOSED 2026-10-22: restriction lifted, daily publishes verified | resolved |
| LinkedIn company specialties | One 60-second manual field: cloudless.gr page → Edit → Specialties → add `Web Development`, `Managed Hosting`, `AI Automation`, `Custom Software` (keep `Digital Marketing`). ArtDeco pill input stays display:none in headless sidecar — 16 automation paths exhausted | Manual pointer only |
| ~~omv Pi outage~~ | RESOLVED 2026-10-24: host hard-down (no tailnet ping, no LAN). Power cycle recovered; k3s unit is `k3s-k3s-omv.service` (not `k3s.service`), watchdog drop-ins auto-started it; 0 non-Running pods, site 200. Diagnostics path: Windows ARP table + scan LAN from omv-ha (`ssh tbaltzakis@192.168.1.130`, key `~/.ssh/id_rsa_win`) | resolved |
| [#784](https://github.com/Themis128/cu130-slim/issues/784) | Publish leg proven (55 FB publishes); Meta console sample → `/api/v1/messenger/webhook` 200 (HMAC verified). Remaining: one real inbound customer message | Owner sends test message from non-admin account |
| [#785](https://github.com/Themis128/cu130-slim/issues/785) | Meta does NOT require business verification (Security Center); app not connected to `cloudless.gr` portfolio — connect blocked by temporary personal-account restriction; `leads_retrieval` not on app | Meta account restriction clears; then connect portfolio → leads use case → App Review |
| [#786](https://github.com/Themis128/cu130-slim/issues/786) | Phone `NOT_VERIFIED`; 136024 cooldown gate fixed+deployed (PR #830): ~1h hold with request log; beat auto-retries `request_code` | Owner supplies SMS code → verify + register PIN |
| ~~[#787](https://github.com/Themis128/cu130-slim/issues/787)~~ | IG private-session reassessment — CLOSED 2026-10-23: Graph covers publishing+DMs+insights live; private session off critical path | resolved |
| [#788](https://github.com/Themis128/cu130-slim/issues/788) | IG profile link → `cloudless.gr/links` (verified 200 live) | IG mobile-app Edit-Profile only (no Graph write path) |
| [#789](https://github.com/Themis128/cu130-slim/issues/789) | Challenge cleared (sidecar logged in); website field eligibility-gated: needs ≥1K followers or TikTok Business verification (account: 4 followers) | TikTok Business verification (owner docs + phone) or follower growth |
| ~~[#790](https://github.com/Themis128/cu130-slim/issues/790)~~ | TikTok Direct Post audit — CLOSED 2026-10-23: console production-approved; `creator_info` returns `PUBLIC_TO_EVERYONE`; `unaudited` restriction lifted | resolved |
| [#791](https://github.com/Themis128/cu130-slim/issues/791) | Local state clean: 0 pending/processing rows, no live publish_ids | Owner reviews TikTok app creator inbox |
| ~~[#792](https://github.com/Themis128/cu130-slim/issues/792)~~ | Slack webhooks — CLOSED 2026-10-23: all `SLACK_*_WEBHOOK_URL` empty, bot-token auth.test live; webhooks inert | resolved (bot-token-only transport) |
| [#793](https://github.com/Themis128/cu130-slim/issues/793) | Partner shortlist — 6 candidates verified live in `Partners & influencers` TargetList, posted to issue 2026-10-23 | Owner approve/drop per recipient |
| [#794](https://github.com/Themis128/cu130-slim/issues/794) | Dry-run done 2026-10-23: 154 pending (145 DM + 9 connect); all touches 401 behind LinkedIn session — gated on LinkedIn 2FA approval | LinkedIn session healthy + owner approves batch size |
| [#795](https://github.com/Themis128/cu130-slim/issues/795) | AI-governance offer / service page | Owner positioning and pricing decision |

### Resolved since 2026-10-21 registration

- **#782, #783** — verified live via Playwright MCP, closed 2026-10-22.
- **#787, #790, #792** — verified live via API/console MCP, closed 2026-10-23.
- **PR #829** — session-healer excludes tombstoned (deleted_at) FB pages from escalation. MERGED.
- **PR #830** — WhatsApp 136024 gate: ~1h hold (not 72h) when a real request log exists. MERGED + deployed.
- **PR #831** — MinIO S3 host port 9100→9102 (node-exporter clash); healthy.
- **PR #828** — `publish_cancelled` reclassified `failed`→`skipped` + historical rows backfilled; bulk-cancel corruption on post `65e67080` repaired (4 targets restored to `published`). MERGED.

### Still blocked on humans / consoles (2026-10-23)

- **LinkedIn session** — `pending_2fa` checkpoint live in sidecar; 429 cleared. Owner approves push in LinkedIn mobile app → next heal persists cookies. Unblocks #794 and LinkedIn analytics sync.

PR #781 (deployment evidence) was authoritatively verified merged at
2026-10-21T12:50:43Z. PR #780 remains the merged implementation record.

## Meta end-to-end audit — 2026-10-21

- [x] **Browser warm/keepalive ownership fix** — PR #780 merged:
  warm now rejects interactive/foreign sessions; keepalive skips busy/interactive
  sessions and stops after page/context replacement. Ten regression cases plus
  39 existing bridge-client tests pass. Full backend suite: 6475 passed,
  11 skipped; changed Python files lint clean; compose config validates.
  Deployed bridge code and restarted after owner approval; live contention
  checks 5/5 passed; foreign warm returned 409 with owner page unchanged.
  Scheduler resumed and confirmed running/unpaused. Updated browser image
  built successfully for future recreations; persistent volumes preserved.
- [ ] **Secondary security email** — fresh Playwright MCP Accounts Center read
  still shows only the existing email and phone, not the requested secondary
  email. Previous success UI did not establish persistence. Add attempts return
  a generic temporary error even for Facebook alone; cause unverified. Owner
  must complete/retry through the trusted mobile app and confirm contact info.
- [ ] **Instagram restriction** — Accounts Center shows `cloudless.gr` locked;
  recent publish failed code 25/subcode 2207050, non-transient user restriction.
  Official profile read works (13 followers, 64 media); quota read reports 24
  remaining. Neither read proves publishing permission. Resolve Account Status
  challenge/appeal before attempting publication; do not bypass with private API.
- [x] **Facebook sidecar session instability — resolved 2026-10-22 (PR #804):**
  root cause was the profile picker's *single-profile* variant — bare
  `facebook.com/` URL (no `crypted_string=`), plain `Continue` button whose
  accessible name is `Continue <Name>` via aria-label, so exact-text matching
  never fired and the handler dead-ended on "Use another profile". The
  `c_user` cookie gate also ran before the picker body check, short-circuiting
  dismissal on fully downgraded sessions. Fixed: `isLoggedIn()` now detects
  the picker by body markers first, auto-dismisses once
  (`allowPickerDismiss` flag, no recursion), and re-evaluates — covering every
  `goto` + `isLoggedIn` handler without per-route wiring. Verified live:
  `/session` → picker detected → dismissed → `logged_in:true`; queue retry
  `deb9da45-615e-44eb-98b1-5eb27074b058` (post
  `2c82267a-f248-477f-955a-abdf5e256c8e`) **completed** on attempt 1 with the
  picker re-challenged and auto-dismissed 3× mid-publish. Meta may still
  re-challenge navigations (account-security flag, #782), but re-challenges
  are now handled automatically; a persistent `profile_picker:true` after
  dismissal or "Use another profile" alone still means a real logout.
- [ ] **Complete live publish/webhook delivery E2E** after restrictions clear:
  no synthetic posts/messages sent during audit. Messenger profile GET succeeds
  with subscription, menu and ice breakers; Threads profile GET succeeds.
  Facebook Page token debug reports valid. Instagram Facebook-host debug probe
  returned HTTP 400/code 2 transient error; diagnostics now preserve top-level
  errors instead of returning misleading null/error-null output.

## Ops — infra health

- [x] **LanguageTool `stack-ops:8010`** — container was being deleted repeatedly;
  recreated via `docker compose up -d languagetool` → 200, real grammar matches.
  **Watch**: if it vanishes again, find what's removing it (external prune?).
- [x] **Slack publish notify** — `SLACK_PUBLISHING_CHANNEL_ID` mapped to api +
  workers (docker-compose). Live delivery verified in `#socialauto`
  (`C0BT263L17U`). Dead webhook 404 remains cosmetic (env var unused path).
- [ ] **Regenerate Slack incoming webhooks** — ALL webhook URLs 404
  (`no_service`, old workspace). 2026-10-20: all 7 `SLACK_*_WEBHOOK_URL`
  **blanked in `.env`** so every path goes straight to bot token (was
  wasting an HTTP call + warning per post). Remapped all stale channel IDs
  (old→new workspace) — `SLACK_CHANNEL_ID`→C0C1F1K3DDF, `ALERTS`→C0C1D46212N,
  `PUBLISHING`→C0C1B8PMTUN, `LEADS`→C0C3XKEHPM1, `SUPPORT/BILLING`→C0C3A9WEX5E
  (PR #773). `notify-failures` error workflow verified end-to-end → real
  alert in #socialauto-alerts. Only leftover: re-create webhooks in the
  Slack app console (user action) if webhook delivery is ever wanted again
  — nothing is functionally blocked.
- [x] **Postgres restarts — investigated 2026-10-20** — recurrence was a
  graceful whole-stack restart ~18:36 UTC (host/WSL-level event, exit=0,
  no OOM), NOT a crash loop. Queue clean (0 processing rows). No sweeper
  needed: the queue claim already reclaims PROCESSING rows with
  `locked_at` >15min stale (publishing.py claim query), and `locked_at`
  is always set atomically with the PROCESSING transition — orphan-free.
- [x] **`/api/v1/ai/generate-content` exceeds ~280s** — FIXED 2026-10-20
  (PR #754). Root cause: `score_brand_compliance._ai_tone_match` hardcoded
  `provider_name="dmr"` (~72s local call behind the DMR slot) regardless of
  the requested provider, and `chroma add_content` was awaited inline (~18s).
  Provider now passes through; chroma index write is a background task; the
  frontend `generateContent` wrapper defaults to `provider='cloudflare'`
  (matching `generateImage`). Live-verified: CF path 95.5s (was >300s
  timeout). DMR path remains the free/local fallback for n8n/celery callers
  on the docker network (no gateway timeout there).
- [x] **Influencer/partner lane — seeded 2026-10-21** (Prakoso et
  al. 2024 rec. 3): 6 real Greek/EU cloud/devrel candidates created as
  EspoCRM Leads (source=Partner) + linked to `Partners & influencers`
  (`6ad70b7459be03ec5`) with pending-review notes. **Next: user reviews
  the shortlist before any outreach** — no auto-contact on this lane.
- [x] **TikTok `url_ownership_unverified` — RESOLVED** (verified 2026-10-20
  via post_targets): the only occurrences are 2026-09-16/17, predating the
  `cloudless.gr` domain verification (done 2026-10-02, tiktok-console-ops).
  No recurrence since. Remaining TikTok publish blockers are different
  classes: `unaudited_client_can_only_post_to_private_accounts` (Direct Post
  audit under review), `spam_risk_too_many_pending_share` (inbox drafts must
  be finished in the app), and `MEDIA_PUBLIC_BASE_URL` unset when the tunnel
  is down — none are code fixes.
- [x] **Facebook Page `Cloudless.gr` status=revoked — triaged 2026-10-21:**
  TWO pages exist in the account — `Cloudless.gr` (revoked, the deleted
  duplicate per facebook-content-ops) and `cloudless.gr` (the canonical
  active page, publishing fine). The revoked row is the dead duplicate —
  no action needed; the active page is healthy. If the revoked row's
  alerts are noisy, archive the SocialAuto account row in the UI.

- [x] **`warp-proxy`** — recreated; `warp=on` verified 2026-10-19.
- [x] **X/Twitter + TikTok token expiry** — TikTok self-healed (23.6h); X was
  refreshed by the token sweep 2026-10-19 (manual refresh verified → 20:17 UTC).

## Autonomy learning loops — 2026-10-22 (PR #822)

Adopted from the agentic-company survey: failure-trajectory learning,
edit-diff self-improvement, earned autonomy, cited recall. All human-gated —
proposals on `audit_logs` + Slack, nothing self-executes.

- [x] **`app/services/failure_learning.py` + daily 10:05 beat
  `run_failure_learning_sweep`** — classifies `post_targets.error_message`
  into a 9-signature taxonomy (tiktok_domain_unverified, spam_risk,
  session_expired, ig_media_missing, x_quota, meta_permissions,
  threads_object_not_found, rate_limited, unhandled_exception); ≥3x/7d →
  one remediation proposal per 3d cooldown (Slack + `audit_logs`).
- [x] **Earned-autonomy ladder** — `scripts/workforce_audit.py` persists a
  `workforce-verdict` audit row per worker per run; ≥10 consecutive clean
  audits proposes graduation (supervised→review→auto), a failure verdict in
  last 7 runs on an `auto` worker proposes demotion. Surfaced in
  render/JSON/Slack.
- [x] **Edit-diff voice learning** — `update_post` snapshots
  `meta_data.original_text` on first human edit (never overwritten);
  `app/services/edit_diff_learning.py` + Sunday 07:00 beat `run_edit_diff_sweep`
  distills deterministic kept-vs-drafted features into voice_signature
  suggestions (≥2 posts AND ≥40% share — single edits are taste).
- [x] **Cited recall** — `company_brain.recall_cited` returns hits with
  `kind`/`day`/`distance` provenance + named `gap` reasons; wired into
  `strategy_report._llm_actions` (dated journal lines, explicit
  no-evidence statement).
- [x] **Workforce registration** — `failure-learner` + `voice-learner`
  added with full ABPM contracts (autonomy=review); 20 workers total.
- [x] **Tests** — 56 new/updated; new modules at 100% coverage; ruff clean.
- [x] **Post-merge deploy** — PRs #822/#823/#824 all merged 2026-10-22;
  `celery-beat` + `social-worker-default` restarted, both tasks registered
  and routed to `default` queue. Live-verified: sweep artifacts in
  `audit_logs` (`failure-learning`×2, `edit-diff`×2, `workforce-verdict`×20)
  and 3 real demotion proposals emitted (email-drip, session-healer,
  analytics-sync — all had recent failure verdicts).

## Resolved this session — 2026-10-22

- [x] **WSL/GPU fixes** — addressed multiple WSL2 + Docker Desktop GPU issues:
  - `scripts/dmr/gpu-runner-recreate.py`: added dynamic WSL lib detection
    (`find_wsl_lib_src()` with 3 strategies) replacing hardcoded path that
    broke on distro upgrades; added `warmup_models()` for primary models
    (qwen3:8b, qwen3-4b-instruct) so they're ready on first request.
  - `docker-compose.yml`: embedding model
    `hf.co/Qwen/Qwen3-Embedding-0.6B-GGUF` now has explicit `context-size: 8192`
    (was default 262144 → 36GB KV OOM on 8GB GPU).
  - `Dockerfile`: ComfyUI `BASE_ARGS` now includes `--lowvram` (keeps text
    encoders on CPU, ~1.5GB VRAM savings).
- [x] **Facebook sidecar profile picker recovery** —
  `facebook-browser-sidecar/server.js`: added `handleProfilePicker()` with 3
  strategies (Continue as button, Use another profile handling, profile card
  selection) integrated into `navigateAndCheck()` for automatic recovery.
- [x] **Instagram private-API rate-limit handling** —
  `app/services/instagram_private_api.py`: added `_call_with_retry()` with
  exponential backoff + jitter (base 2s, max 60s, 5 attempts) applied to
  `login()`, `login_by_sessionid()`, and `challenge_resolve()` for 429
  `ClientThrottledError` from aiograpi sidecar.
- [x] **Slack webhook regeneration runbook** — created
  `docs/ops/slack-webhook-regeneration.md` documenting the full process:
  recreate Slack app from manifest, create incoming webhooks per channel,
  verify with `slack_tool.py webhooks`, and the stale-env recreation caveat.
- [x] **stack-ops prewarm endpoint** — added `POST /prewarm` to
  `stack-ops/stack_ops.py` that wakes critical services in parallel
  (linkedin-browser-sidecar, facebook-browser-sidecar, tiktok-browser-sidecar,
  instagram-private-api, warp-proxy, languagetool, comfyui) with optional
  custom service list via JSON body.

## Open PRs / merges to verify

- [x] **PR #737** — IG Graph permalink pk→shortcode fix + swept payloads.
  MERGED 2026-10-19.
- [x] **PR #741** — WhatsApp lead-gen Flow promotion + `send_flow` param fix.
  MERGED.
- [x] **PR #742** — AIE email-course drip sequence. MERGED; workers
  (`social-worker-default`, `celery-beat`) restarted to load the drip steps.
- [x] **cloudless.gr PR #2228** — `/links` live social proof. MERGED
  2026-10-20 (subscriber count via `Contact/leadSource=Email`,
  `unstable_cache` for the dynamic route, conditional attribution).
- [x] **PR #754** — MERGED; follow-ups #757 (bounded chroma indexing,
  db/team threading), #758 (remaining compliance callers), #759 (n8n
  Slack recipe → bot token), #760/#761 (engagement-ops skill + messaging
  fix), #762 (cross-channel + predictive skills) all merged 2026-10-20.
- [x] **PR #739** — skills sync merged `2026-10-19T12:48Z`.
- [x] **PR #740** — Oxford AIE docs merged (`5c1d2ff6`).
- [x] **PR #773** — Slack channel-ID remap + notify-failures body fix.
  MERGED; end-to-end verified with a forced failure → real #alerts post.
- [x] **PR #774** — n8n 2.43.4, durable scheduler, external task runners,
  metrics proxy, retention, PG16→17 cutover, IntraVox digest fix,
  Prometheus n8n target. MERGED.
- [x] **PR #775** — LinkedIn newsletter nurture lanes (sidecar
  profile-DM + invite, client, CLI). MERGED 2026-10-21.
- [x] **cloudless.gr PR #2232** — EU AI Act checklist lead magnet.
  Authoritative GitHub status rechecked: MERGED 2026-10-21T08:23:40Z.
  No remaining merge blocker; prior auto-merge/CI note was stale.
- [x] **PR #797** — Telegram channel-ops guides (`.devin` + `.cursor`)
  synced to the absolute MCP launcher from PR #778. MERGED 2026-10-21
  with all checks green; closes the Devin Review finding on #778.

## Blocked on humans / consoles

- [ ] **Meta `leads_retrieval` App Review** — paid lead-ad delivery gated;
  blocked on Meta business verification. Webhook code is armed on both paths.
- [ ] **WhatsApp phone registration** — `+30 697 777 7838` is
  `code_verification_status: NOT_VERIFIED` → Cloud API sends 133010
  "Account not registered". **SMS code needed**: PR #830 (merged+
  deployed) fixed the 136024 gate — ~1h cooldown with request log,
  beat auto-retries `request_code` on schedule; when the user can
  receive the SMS, run verify-code + register (whatsapp-ops scripts).
  Flow promotion is armed and will activate automatically once
  registered.
- [x] **LinkedIn org profile write — VERIFIED STALE 2026-10-21:** the
  cloudless-gr Company Page (`urn:li:organization:108614163`) is already
  fully populated — website `cloudless.gr`, CTA "Visit website"→cloudless.gr,
  complete description, 10 specialties, phone. Confirmed via the sidecar's
  internal voyager lane: `canUpdateOrganizationProfile: true` — future
  writes can go through the browser sidecar (no `rw_organization_admin`
  re-auth needed).
- [x] **Instagram private-API (aiograpi sidecar) — NOT NEEDED**
  (closed as #787, 2026-10-23): reassessment verified Graph API covers
  all operational requirements — publishing (52 targets, latest
  2026-10-22), DM bot (`get_conversations` + `send_dm` live), insights.
  DM poller ladder is Graph-primary → browser-bridge fallback; the
  private session is off every critical path. Its only remaining use —
  profile bio/url edits — is mobile-app-gated anyway (#788). No
  reconnect, cooldown, or challenge work required.
- [ ] **Instagram bio → `/links`** — website/name edits are mobile-app-only
  (hard platform limit; do not force API/sidecar paths).
- [ ] **TikTok bio/website** — captcha challenge cleared (sidecar
  logged in 2026-10-23), but the website field is eligibility-gated:
  ≥1,000 followers or Registered Business Account required (account
  has 4 followers). Domain already in bio as plaintext. Path: TikTok
  Business verification (Settings → Business verification, owner docs)
  or follower growth — see #789.
- [x] **LinkedIn newsletter outreach — BUILT + verified 2026-10-21**
  (PR #775): `scripts/newsletter_nurture.py` drives the cohort through two
  sidecar lanes — `message` (146 first-degree, Voyager createMessage) and
  `connect` (10 non-1st-degree, verifyQuotaAndCreateV2 invite); 12
  follow-only skipped. Dry-run default, `--approve` to send, 20/day cap,
  45–120s pacing, `New`→`In Process` + CRM note per touch. **Verified
  live:** real DM confirmed in Voyager conversation GraphQL; real invite
  confirmed "Pending" on the recipient profile. **Next: user approval for
  paced batches** (`run --approve --limit 5` suggested; soft non-sales copy
  already templated).
  **Verified 2026-10-19:** this IS the funnel's `lead→nurtured` break —
  the leads carry LinkedIn profile URLs in `description`; LinkedIn via the
  sidecar is the only reachable nurture channel (only 3 emails on file,
  all self/test).
- [x] **EspoCRM: `LinkedIn newsletter` TargetList restored 2026-10-20** —
  the 168 newsletter leads were mis-attributed to `Social engagement
  (replies)` (entryCount exactly 168, all members source=Newsletter, no
  reply notes). Recreated `LinkedIn newsletter` (`6ad6be0fa76d3000c`),
  re-linked all 168, unlinked them from the engagement list (now 0). The
  engagement sweep only re-adds genuinely reply-engaged leads from here.

## Growth / funnel

- [x] **Bio links → `/links`** — FB Page website + Threads bio updated
  2026-10-19 (IG/TikTok/LinkedIn blocked — see above).
- [x] **Social proof on `/links`** — live-count + featured-testimonial block,
  cloudless.gr PR #2228.
- [x] **WhatsApp Flow promotion** — `_maybe_promote_flow` armed: greeting +
  business intents → `cloudless_lead_gen` (1971236133545424), 7-day resend
  cap. Activates once number registers (see blocked).
- [x] **Oxford AIE email course** — sequence engine + 4 seeded drip_template
  campaigns (`aie_course_1..4`), 7-day steps, PR #742.
- [ ] **AI-governance audit service page** on `cloudless.gr/pricing|/contact`
  — user decision (positioning, pricing).
- [x] **"EU AI Act readiness checklist" lead magnet — DONE 2026-10-21**
  (cloudless.gr PR #2232): `scripts/generate_eu_ai_act_checklist.py` →
  `public/eu-ai-act-checklist.pdf` (Reg. (EU) 2024/1689, verified dates),
  `ChecklistSignup` parameterized by magnet slug + copy, second signup
  card live on `/links`. `/api/subscribe` already tags any magnet slug
  into EspoCRM.
- [x] **Post-merge worker restart** — done 2026-10-19:
  `social-worker-default` + `celery-beat` restarted; #742 drip steps loaded.

## Done recently (reference)

- [x] **Instagram web session restored 2026-10-20** (alert: "session expired
  for @cloudless.gr"): stored sessionid was genuinely dead (302 +
  `require_login`); bridge saved-profile card login → feed live as
  `cloudless.gr` (verified via in-session `web_form_data` fetch);
  `/session/extract` persisted 6/6 fresh cookies; `web-session` record
  updated (sessionid/csrf/ds_user_id encrypted, `login_type=web_session`);
  healer sweep `unhealthy: []`. Validator probe `/users/{id}/info` shows
  HTTP 429 = endpoint throttling, not expiry. Private-API sidecar remains
  blocked — see open item above.
- [x] **Backlog infra triage 2026-10-19 (via platform/slack/n8n MCPs):**
  LanguageTool back up (200 `/v2/languages`, container restarted);
  `warp-proxy` + `facebook/tiktok/messenger` browser sidecars were stopped →
  started, all health 200, SOCKS5 open (instagrapi path restored);
  Slack bot-token publish path verified (`ok:true` → #socialauto);
  `publish_queue` clean — no orphaned `processing` locks (288 completed /
  1 failed); `refresh_expiring_tokens` beat verified, X token manually
  refreshed → new expiry 20:17 UTC; session-heal sweep clean
  (`unhealthy: []`, contended = busy not dead); analytics sync errors →
  `errors: []` once sidecars up (follower_snapshots is a change-log —
  writes only on delta or 7-day heartbeat, so "staleness" is flat counts).
  DMR online — see generate-content latency item.
- [x] Ops report 2026-10-19 triaged: LinkedIn "failure" = historical noise;
  Facebook post rewritten (Sofia NLP 100, was 75) + requeued → **published**
  <https://www.facebook.com/116436681562585_122244667814100600>
- [x] Prometheus per-platform metrics + Grafana `Social Platforms` dashboard
  (PR #736 merged `b8f99e40`); EspoCRM exporter + `EspoCRM Vitals` dashboard
  live in k3s.
- [x] OMV apticron: `openmediavault-omvextrasorg` 8.0.4 + `openmediavault`
  8.5.12-1 applied over Tailscale.
- [x] LinkedIn newsletter funnel: 168 subscribers → EspoCRM leads, PR #735 merged.
- [x] Oxford AIE brochure → 4 `ContentPromptTemplate`s live + verified via
  `/generate-content` (SEO 91).

## omv Docker state wipe + workspace restore — 2026-10-24

**Incident:** `cloud.cloudless.gr` → Cloudflare error 1033 (~21:56 EEST).
omv hard-rebooted ~21:43 (same power-cycle event as the registered omv outage).
Boot-time `docker.service` failed on the iptables bridge race
(`Unable to enable ACCEPT OUTGOING rule`); the socket-activated dockerd came
up with **zero containers/images** — the sda1 docker-root metadata
(`containers/`, `image/`) was wiped during the unclean shutdown, along with
`compose-files/workspace/workspace.yml` + `workspace.env`
(override stub + php-conf.d survived; all sdb1 data intact).

**Restore:** `workspace.yml` redeployed from repo
`deploy/omv-workspace/docker-compose.yml` (NC 35.0.2), `workspace.env`
re-extracted from `omv-confdbadm read conf.service.compose` (11 vars, never
printed), stale bridge networks pruned. `docker compose up -d` for
workspace + `~/cloudflared` + searxng + tempo → 15 containers healthy.
Verified: `cloud/status.php` 200 (NC 35.0.2, no db upgrade needed),
`office` 200, `signal` welcome 200, `push` answers (400 = auth-gated, reachable).

**Left over:**
- [ ] OMV plugin DB (`conf.service.compose` → files.file[name=workspace].body)
  still holds the **old NC34 compose** — a GUI save regenerates the old
  `workspace.yml` and downgrades/undoes the repo version. Sync DB body to
  repo compose or edit via OMV Workbench.
- [ ] Root cause of the metadata wipe unproven (journald only kept current
  boot). If it recurs, consider enabling persistent journald +
  `docker.service` ordering hardening (the 10-after-k3s/waitAllMounts
  drop-ins didn't prevent the iptables race).
- [x] `php-conf.d/zz-jit-off.ini` (opcache.jit=disable, segfault hardening)
  mounted into nextcloud/nextcloud-cron/nextcloud-worker — shipped via
  this session's PR; previously only lived on-host.


## Session ops notes — 2026-10-25 (Docker Desktop check)

- Ran full stack audit: no OOMKilled containers, all core healthy; `Exited`
  containers were stack-ops idle-sleep (by design), portainer exit=2 benign.
- Found WSL utility-VM memory exhaustion: swap 8.0/8.0 GiB full, kernel page
  allocation failures in dmesg, VRAM 7.6/8.2 GiB with 7 DMR models resident.
- Fix applied: evicted/let DMR models expire (VRAM → 1.9 GiB), then
  `wsl.exe --terminate docker-desktop` (surgical VM restart; Ubuntu session
  unaffected). Swap → 632 MiB, RAM available 3.6→10 GiB. All containers
  self-recovered via restart policies in <1 min; social.cloudless.gr 200,
  n8n.cloudless.gr 302, 4 Celery nodes pong, cf-db health all true.
- Known standing noise: `dmr-watchdog` logs `config apply failed — 409
  Conflict` whenever target models are loaded; configs land on next load.
  By design, but noisy — candidate fix: suppress/tolerate 409 in watchdog.
- Recurrence path: if swap/page-alloc failures recur, the WSL utility VM
  needs `.wslconfig` memory/swap tuning or periodic docker-desktop restarts.

### Follow-up fixes landed — 2026-10-25 (same session)

- [x] **dmr-watchdog 409 noise** — `configure()` now reads HTTP status via
  `wget -S`: 2xx (the endpoint returns 202, not 200) = applied, 409 =
  "model loaded, config lands on next load" counted as pending, anything
  else = real error. `apply_configs` logs only on pending-count
  transitions + real errors. Verified live: "all canonical model configs
  applied", no more per-minute 409 spam. `docker-compose.yml`.
- [x] **Swap-exhaustion recurrence** — `WSL-Memory-Watchdog`
  (`D:\DevOptimizer\scripts\check-wsl-memory-pressure.ps1`, every 30min)
  now auto-remediates at >=95%: `docker desktop restart` (fallback
  `wsl --terminate docker-desktop`) with a 2h cooldown stamp
  (`logs\dd-restart.cooldown`) + event 1003. No .wslconfig change — 16GB
  swap was the commit-balloon cause, keep 8GB. Note: post-restart swap
  refill to ~60% is normal cold-anon eviction with ~9GB RAM available.
- [x] Confirmed `--n-gpu-layers 0` (CPU-pinned DMR fleet) is canonical in
  `dmr.py::_BEST_PRACTICE_CONFIGS` — the recreated watchdog now ships it;
  GPU stays free for ComfyUI.
