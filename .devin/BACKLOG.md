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
| [#782](https://github.com/Themis128/cu130-slim/issues/782) | Secondary Meta recovery email persistence | Owner / Meta verification |
| [#783](https://github.com/Themis128/cu130-slim/issues/783) | Instagram lock and publishing restriction | Account Status recovery |
| [#784](https://github.com/Themis128/cu130-slim/issues/784) | Real publish and inbound-webhook E2E | Recovery and test-send approval |
| [#785](https://github.com/Themis128/cu130-slim/issues/785) | Meta business verification / lead App Review | Owner evidence / Meta decision |
| [#786](https://github.com/Themis128/cu130-slim/issues/786) | WhatsApp verification and registration | Owner ready for fresh code |
| [#787](https://github.com/Themis128/cu130-slim/issues/787) | Instagram private-session reassessment | Resolve restriction, respect cooldown |
| [#788](https://github.com/Themis128/cu130-slim/issues/788) | Instagram approved funnel profile link | Supported app / owner action |
| [#789](https://github.com/Themis128/cu130-slim/issues/789) | TikTok profile website | Owner solves challenge |
| [#790](https://github.com/Themis128/cu130-slim/issues/790) | TikTok Direct Post audit | Platform review |
| [#791](https://github.com/Themis128/cu130-slim/issues/791) | TikTok unfinished inbox shares | Owner reviews drafts |
| [#792](https://github.com/Themis128/cu130-slim/issues/792) | Optional legacy Slack webhook replacement | Not a functional blocker; owner choice |
| [#793](https://github.com/Themis128/cu130-slim/issues/793) | Partner shortlist review | Approval before outreach |
| [#794](https://github.com/Themis128/cu130-slim/issues/794) | Paced newsletter nurture batches | Approval after fresh dry run |
| [#795](https://github.com/Themis128/cu130-slim/issues/795) | AI-governance offer / service page | Positioning and pricing decision |

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
  "Account not registered". **SMS code needed**: request_code currently
  rate-limited (136024/2388091); when the user can receive the SMS, run
  verify-code + register (whatsapp-ops scripts). Flow promotion is armed and
  will activate automatically once registered.
- [x] **LinkedIn org profile write — VERIFIED STALE 2026-10-21:** the
  cloudless-gr Company Page (`urn:li:organization:108614163`) is already
  fully populated — website `cloudless.gr`, CTA "Visit website"→cloudless.gr,
  complete description, 10 specialties, phone. Confirmed via the sidecar's
  internal voyager lane: `canUpdateOrganizationProfile: true` — future
  writes can go through the browser sidecar (no `rw_organization_admin`
  re-auth needed).
- [ ] **Instagram private-API (aiograpi sidecar) login rate-limited** —
  `POST /auth/login` returns `429 ClientThrottledError` on
  `caa.login.async.send_login_request` even via `warp-proxy` SOCKS5
  (verified 2026-10-20); earlier same-day attempts failed at transport
  (DNSError/Timeout). Web `sessionid` import still dead for mobile calls
  (`login_required`) — confirmed again. Retry credential login after a
  24–48h rate-limit window; do not burn attempts before then.
  **Unblocked meanwhile:** bridge web session for `@cloudless.gr` restored
  (saved-profile card → live feed, verified via in-session
  `web_form_data`), fresh 6/6 cookies persisted, `web-session` store now
  holds the correct `ds_user_id 36910035385` (the previously stored
  `32323137563` was a stale/foreign ID). Web-API publish path
  (`rupload_igphoto`) works; only instagrapi/sidecar calls stay down.
- [ ] **Instagram bio → `/links`** — website/name edits are mobile-app-only
  (hard platform limit; do not force API/sidecar paths).
- [ ] **TikTok bio/website** — writes captcha-blocked.
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


## Session leftovers — 2026-10-25 (n8n/IntraVox ops day)

Done: digest pages Oct 16-19 backfilled in IntraVox (real DB data); `socialauto-daily-slack-digest` `Summarize Result` now throws on failed IntraVox write (commit 1d2d837e); `lead-enrich`/`newsletter-nurture` workflows imported+published on dev-box n8n (`function`→`code` node fix, webhooks verified registered); n8n LAN-bound (`192.168.1.23:5678`+loopback), compose image aligned 2.43.4; `.env` `SOCIAL_POSTGRES_PASSWORD` synced to live value; stale `deployment_key` rows cleared+regenerated; omv cloudflared repointed `n8n.cloudless.gr`→`192.168.1.23:5678` (verified 200); `cloudless-secrets` `N8N_API_URL` patched live (pod→n8n 200); cloudless.gr PR #2284 open with all doc/config fixes.

| Issue | Remaining work | Gate |
| --- | --- | --- |
| ~~n8n `N8N_API_KEY` empty~~ | DONE 2026-10-25 — minted via DB insert (`user_api_keys`, JWT signed by derived instance secret); verified `GET /api/v1/workflows` → 200; written to `.env` | — |
| Cloudflare Access: n8n.cloudless.gr has no service-token/webhook bypass | Browser access works; server-side must keep using LAN URL | By design — revisit if external callers need webhooks |
| `test/backend-platform-coverage` branch | Carries session commits `1d2d837e`,`120277a5`,`dfe3bdf8` — merge via PR when reviewed | PR/merge decision |
| uncommitted: `linkedin-browser-sidecar/Dockerfile` | Pre-existing local change, not from this session | Owner review |
| ~~Stored n8n credentials dead~~ | DONE 2026-10-25 — all 6 re-encrypted under current key via DB (round-trip verified); TOTP cred stores empty secret — harmless: no SocialAuto user has two_factor_enabled (auth.py ignores otp) | — |
