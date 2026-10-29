# BACKLOG (living)

## Done 2026-10-27 (PR sweep — umbrella merge)
- Skool→IntraVox pipeline: Sofia Kakkava community content now has an IntraVox hub (page `kakkava-visibility-era`, nav under Visibility Era & Social). TODO: scheduled `trend_scan`→IntraVox `en/news` digest. **Blocked**: shared browser bridge returns 409 'busy with facebook session' — needs retry-with-backoff in `skool_sync` or a dedicated sidecar; also needs one manual Skool login on browser-novnc to seed the session.
- Celery send_task anomaly (2026-10-27): `app.worker.tasks.email_campaigns.send_campaign` enqueued via `celery_app.send_task` from social-api returns a task id but never reaches workers (PENDING forever; `inspect reserved/scheduled` empty) — while other tasks on the same `default` queue flow fine. Bypass: run `send_campaign()` service directly in social-api container. Repro: send_task id `79229b44` stayed PENDING. Needs delivery-tag/routing instrumentation.
- All open PRs landed on master via merge-commit umbrella #954: #947 (publish-queue
  time budget), #944 (review-943 fixes), #927 (social-auth-ops), #926 (ai-video-ops
  skill), #910 (arch docs), #895 (redis-py 7.x), #894 (compose watchdog),
  #887 (sniffer tools), #868 (governed workers) — plus #950 merged directly.
- skool_sync.py: added missing browser-bridge helpers (_bridge_nav/_bridge_eval/
  _bridge_call + imports) — mypy name-defined x7 fixed before landing.
- cloudless.gr: umbrella PR #2305 (auto-merge armed) carries 10 PR heads; CI
  fixes applied (GSC page restored, agency board -> /admin/analytics/agency,
  survey route getConfig fix, metabase workflow -> kubectl shim).

## Leftovers
- cloudless subset PRs (#2294/2295/2296/2292/2289) to close once #2305 lands —
  their content lands via tops; diffs shrink to nothing.
- dependabot #2301/#2302 rebuilt on main + auto-merge armed (lockfile regen).
- X re-auth, LinkedIn li_at rotation, Community Management API submission —
  unchanged from prior entry.
- 2 live posts link cloudless.com not .gr — still awaiting go-ahead.

## Done 2026-10-25
- Survey funnel + platform-aware nurture (#909 platform field fix; meta_data.platform drives PLATFORM openers)
- Video ops: trim/convert/attach-audio/mute/extract + mkv/avi upload (#908)
- Media-sound rule in AGENTS.md (#907); attach-audio enforces it
- Spellcheck gate before render: carousel copy + ad creatives (#915)
- platform_display() canonical casing (#913); cloudless.com→.gr domain guard (#916)
- Tests: 32 total — platform_coverage(16) + full_spectrum(9) + media_quality_gates(7) — all green
- Agency metrics endpoint /api/v1/agency/metrics — TCD/TSR/PPL/ALV/HIPC (#917)
- /admin/analytics ops board (cloudless.gr #2295); metabase-fix workflow (#2294)
- publish_retry sweep — failed targets requeue (15min beat); kakkava_sync settings fix
- human-gate-ops scripts: x-reconnect/x-status/cmapi-check/check-inbox
- Mail: signature wired + Nextcloud sync; mailbox triaged (funnel notifications verified)
- Funnel docs: Nextcloud Funnels/ + docs/funnel-media-architecture.md (#910)

## Leftovers (human gates)
- X re-auth: click x-reconnect.sh output URL (works — verified live)
- LinkedIn password + li_at rotation (exposed earlier in session)
- Community Management API: dedicated 2nd app dev-portal submission
- 2 live posts link cloudless.com not .gr — delete+recreate awaiting go-ahead (fb 7c7948a4, li bd301640)
- Threads business profile: browser-session edits (bio/name) — threads-ops scripts ready
- Stacked-branch conflicts recur — prefer fresh-off-master topic branches
- SMB offer survey: clean draft `9f98e2ce-24da-46f3-a19f-8d7e0e875270` ready — owner attaches audience (`offer_survey.py audience … --espocrm`) + sends; Q5 answers pricing → unblocks service page (#795). Skills: cloudless-code-of-honor, signature-offer-ops, offer-survey-ops


## Session 2026-10-27 — strategy-brief parse fix + playbook→queue loop

- **Landed (this branch):** `_parse_actions` now recovers Python-repr
  dict-lists (single-quoted `[{'platform':…}]` replies were discarded →
  rule-based fallback on 2026-10-19/24/27); `_action_entry` recomposes
  `{platform,time,format,reason}` dicts into routable action lines;
  `execute_report_actions` schedules the brief's playbook through
  `monetization_executor.schedule_report_actions` (source=strategy-brief,
  dedup=strategy_action, target=tomorrow); executor gained `source`/
  `dedup_field`/`max_actions`/`target_date` params + a video-only guard
  (tiktok/youtube → draft, never image media); notebook
  `daily_strategy_brief` has `execute=True` + connected-accounts gate;
  brief email now shows a "Queued by the platform" table.
- **Still open:** DMR ReadTimeout on the 06:00Z run (gpu-arbiter 480s →
  unserialized; ComfyUI contends the 8GB card) — consider reserving a slot
  or making the CF Workers AI fallback trigger on ReadTimeout. Facebook
  insights 400 `(#100) nonexisting field (insights)` new since 2026-10-26 —
  check deprecated metric on personal profile. UTM coverage still ~0
  (1/195 posts) — auto-append utm_source in publishing pipeline.
- **Owner-visible:** briefs now auto-schedule tomorrow's playbook;
  drafted (not scheduled) items appear in the UI for media fixes.


## 2026-10-27 — X OAuth1 fallback + PR sweep

- TwitterAPIClient now retries 401/403 via OAuth1Session (user-context) — fixes dead OAuth2 grant when the secret rotates. Wired through `_send` on all 12 call sites; `x_tool.py` routes through it too. Verified live: `validate_token` returns `@TBaltzakis` (id 2046380838477041664).
- 30 PRs → auto-merge queued; 13 conflicting branches rebased + pushed; merge queue drains via CI.
- `feat/phone-tiktok-skills` (PR #941): phone MCP + TikTok resubmit/IG/X scripts — pending CI.
- Slack sweep: alerts are transient worker timeouts (recovered); 2 engagement-gate reposts pending manual review by design.
- Still open: TikTok verification (sidecar login or manual Edit); engagement-gate repost approvals; real X creds if OAuth2 reauth is wanted later (OAuth1 covers posting/DM now).


## 2026-10-27 — PR #948 review follow-ups (branch fix/pr-948-viral-meta-forwarding)
- dispatch worker now forwards `c.meta_data` (viral flag) into service candidate — fixes lost duet_flag gating
- test_dispatch_full_sweep: fixed stale meta_data assertion + added missing gated-items fake result (slack 4→2)
- _Res.scalar() added for daily-cap count query; new test_dispatch_viral_tiktok_becomes_gated_duet_flag (ingest→dispatch)
- 49 engagement unit tests green in social-api container; pushed as branch (PR #948 head lives on bot fork — merge/cherry-pick needed)

## 2026-10-27 — Full PR sweep completed (all open PRs landed on master)

- Swept and merged every open PR to protected `master`: #950 (rebased, engagement ping-once kept), umbrella #954 carrying 9 stacked/conflicted branches (#947, #944, #927, #926, #910, #895, #894, #887, #868) — all auto-marked merged on land.
- #956 resolved+merged (kept master's `meta_data=None` convention + ping-once tests, kept PR's `or {}` safety).
- #957 resolved+merged — five-way alembic head merged to single head `f7a8b9c0d1e2`; took master's slot-hop test assertions (from #963), add/add migration file auto-merged identical.
- #958 (this file's earlier update), #960, #961, #963 merged by parallel sweeps — repo now has zero open PRs.
- Fixed en route: `skool_sync.py` shipped undefined `_bridge_nav`/`_bridge_eval`/`json` (mypy ×7 + runtime break) — added helpers following the session_healer bridge pattern before landing #954.
- Leftovers: none repository-side. Live-stack items remain: restart workers per AGENTS.md after publishing/worker code changes, TikTok sidecar verification still manual.

## Leftovers — 2026-10-27 eod
- **X_CLIENT_ID/X_CLIENT_SECRET** — not in secret store; add via `secret_store.set('X_CLIENT_ID','…')` + `x_reauth.sh` to fix twitter=expired
- **WhatsApp phone verify** — `wa_verify.py` (dual-poll ADB/mail) ready; request_code via whatsapp_tool
- **LinkedIn/X bridge sessions** — 409 busy = shared bridge contention; li_export_request.py + x_oauth_approve.py need one live session each (healer covers)
- **export_datalake OOM** — fixed by per-table streamed writes (#1014); monitor next 6h beat for a clean run
- **PR-landing guard** — never push when `git log -1 == origin/master` (empty cherry-pick closes the PR; documented in pr-landing-ops)

## Session 2026-10-28 leftovers
- [ ] X OAuth approve — fresh auth_token needed in .env OR click reauth_urls URL; bridge pin now holds session 600s (PR #1051)
- [ ] LinkedIn dev portal — grant "Member Data Portability API (Member)" product → li_member_snapshot.py + consent healer auto-harvests connections (PR #1046)
- [ ] WhatsApp — standard registration via POST /{phone}/register (NOT request_code/verify_code — those are migration API; 2388091 decoded)
- [ ] heal_consents hourly beat — twitter/linkedin/whatsapp/bridge probes, Slack alerts on state flips (PR #1049 + #1053)
## Session 2026-10-28 — Shift.Forward() 2026 recap adopted across platforms

- AX Pass dashboard accessed (Gmail code fetch via playwright) → reports saved locally `~/Downloads/shift-forward-recap/` + archived to Nextcloud `SocialAuto/Shift-Forward-2026/` (2 PDFs + 2 MD).
- Company brain: 4 memories injected (`shift-forward-2026:*`, kind=event-recap) — IOC/governance, loop engineering, content angles, CAIPE/n8n eval queue. Retry needed after GPU-arbiter 480s timeouts; run `remember()` in `social-worker-default`, NOT social-api (137 OOM kill — same 1GiB hazard as full pytest).
- 4 DRAFT posts inserted directly (skipped `schedule_report_actions` — it OOM'd mid-copygen in social-api): linkedin org `855d6ede`, fb page `0e57b694`, ig biz `e299006a`, threads `88d1379d`, all `meta.source=shift-forward-recap`, `needs_media=true` — review/schedule in UI.
- IntraVox page `shift-forward-2026-recap` (root, en) — takeaways + links. NOTE: `page-create --parent` returns 403 for non-root parents with app-password creds; root works.
- cloudless.gr blog post `multi-agent-systems-governance-smb` in `src/lib/blog.ts` → PR Themis128/cloudless.gr#2312, squash auto-merge armed.
- Nextcloud Note "Shift.Forward() 2026 — Recap & Adopted Takeaways" in "Company Brain" category (id 1319072).

## Session 2026-10-28 (cont.) — auto-healers for gaps hit this session

- `scripts/compose-watchdog.sh` hardened + scheduled: portable `cd` (was hardcoded /home path), new daemon-death check (docker info fail → `docker desktop restart`, 30min throttle — this is the npipe-blip fix), OOMKilled detection on core containers (restart + Slack alert), social-postgres added to CORE list.
- Scheduled task `SocialAuto-StackWatchdog` (user-level, every 10min, `conhost --headless` Git Bash) — verified registered + fired.
- New `scripts/heavy-exec.sh` — runs ad-hoc python in `social-worker-default` (1.25GiB, idle) instead of social-api (1GiB, OOM-kills at 137); rotates to next worker on 137. Use for remember()/schedule_report_actions/pytest-class work.

## Session 2026-10-28 (cont.) — DMR + healer optimization

- `gpu_arbiter.dmr_slot()` gained `max_acquire_s` + `fail_on_timeout` — embeddings now bound the Redis FIFO wait to 30s and raise ConnectionError on timeout (fails fast into the CF BGE-M3 fallback) instead of stalling 480s then running unserialized. Inner `await_media_idle` re-check bounded by the caller's acquire budget. Files synced via `docker compose cp` into all 6 app containers; workers + api restarted.
- Embedding model `keep_alive` 60s→15m in `_BEST_PRACTICE_CONFIGS` (dmr.py) + dmr-watchdog config (docker-compose.yml:1909 — takes effect on watchdog recreate). NOTE: containers mount the WSL clone (/home/tbaltzakis/cu130-slim) — repo edits live on Windows checkout `feat/gmail-ops-v2`; mirror to WSL clone or merge to master for durability.
- compose-watchdog.sh check #5: `gpu:dmr_lock` held 3+ consecutive 10-min checks → Slack alert (starvation/leak detection).

## Founders' Circle (Sofia Kakkava) — week of 2026-10-28

- [ ] Week 2 actions ("The offer that sells") — recording https://youtu.be/vHlKLsSF4rM · slides https://canva.link/dzabh086dzjph5v
- [ ] Week 2 homework → due before Week 3 (Nov 3) — mastersheet: https://sedate-ulna-278.notion.site/Learners-Founders-Circle-Mastersheet-3ff172ca8fc080b0ac85e0ea6b2673e9
- [ ] Business Mastery Bootcamp Oct 30–Nov 1 — live offer pitch + feedback; expected outputs: refined ICP + core offer, sales-conversation practice
- [ ] Prime bootcamp/YES-Maxxing outcomes into offer_survey copy + 4-YES diagnostic after the sessions
- Mastersheet Zoom: https://us06web.zoom.us/j/88010824977

## 2026-10-30 session closeout

**Landed**: Qualtrics CM-API standard-tier submitted (real-browser playwright driver — `qualtrics-ops/qualtrics_real_fill.cjs`), `engagement_ingest` 15-min keyword feed (PR #1164), `token_expires_at` persistence fix — FB re-renew thrash (PR #1166), alertmanager single-node cluster fix (PR #1168), `cdp-watch-ops` (PR #1162), `li-vetting-watch` (PR #1152), `viber-bot-ops` portal→app correction (PR #1156), `wa_verify` Meta-sender filter, `adb_sms` shared reader.

**Armed watchers**: `cdp_watch` (pid) → auto-completes LinkedIn consent on :9222 · `wa_code_watch` → WhatsApp post-cooldown · `li-vetting-watch` → Telegram on review email.

**External gates (not code gaps)**:
- `chrome-debug-launch.bat` on Windows → :9222 → LinkedIn gmail-code consent completes
- WhatsApp: Meta SMS cooldown — `request_code` bounces ~1h, armed
- Viber: App Token from the mobile app's bot Edit Info (portal retired)
- Bluesky: app-password rotate at bsky.app → `.env`

## 2026-10-30 session 2 closeout

**Landed**: `content_sanitizer` (emoji shortcode→emoji, connective dedupe, near-dup gate — PR #1182), `slop_scan`+`voice_check` (PR #1183), generation-side gate wired into `brand_agent` with prior-output exclusion (PR #1184), TOTP secret-log fix (PR #1180), `fb-reauth-ops` (PR #1178), `pending-triggers-ops` (PR #1172), `win-trigger-ops` AtLogOn task registered live (PR #1173), `viber_app_token` ADB-UI capture (PR #1175), `cdp-watch-ops` (PR #1162). omv node recovered via power-cycle (was hard-down — root SD 80%, /home 28G → sda1 move is the real fix).

**External gates**: `:9222` auto-fires on logon (CloudlessDebugChrome task). Viber = paid commercial channel (€115/mo) — decision, not a build.

- `n8n-workflows/governed-health-agent.json` live in n8n (id `governed-health-agent`, published, daily 08:30 Athens + webhook `governed-health-agent`). AX Pass pilot: Intent Contract node declares goal/scope/forbidden-actions per run; read-only probes (stack-ops /status + social-api /health); deterministic Evaluate (rules before LLM — loop-engineering discipline); Slack human-gate on DEGRADED/ACTION_NEEDED — agent takes no action itself. Dry-run verified: verdict HEALTHY, 21 services checked.
- Docker Desktop wedged 3x in ~1h — watchdog self-healed 1, manual restart for 2nd. Chronic-escalation counter added to check #0.

## 2026-10-28 — owner-gate skill push (session closeout)

- **#786 WhatsApp**: `wa_verify_daemon.py` running (PID /tmp/wa_daemon.log) — hourly request_code retries for 3d + ADB/mail dual-poll + auto verify. Fully unattended.
- **X reauth**: fresh OAuth URL generated via `reauth_urls.py`; `x_oauth_approve.py` ready. BLOCKER: stored `X_WEB_AUTH_TOKEN` is stale — x.com redirects to onboarding. Needs ONE x.com login on adhoc noVNC (localhost:6085) → then the approve script finishes it. `_bridge_health` healer will Slack-alert the staleness hourly.
- **LinkedIn org reauth**: fresh OAuth URL generated; shared bridge is TikTok-locked — approve click pending bridge release. LI sidecar session is live (has_session:true) but task-purpose only.
- **#789 TikTok**: `business.tiktok.com` verified logged in on adhoc browser (localhost:6085) — cert-upload surface staged. Physical: upload ΓΕΜΗ certificate.
- **#788 Instagram**: staged at login wall — one login bootstrap → `/account/external-url` private-API becomes permanent.
- **#785 Meta**: facebook-browser-sidecar has NO session (has_session:false) — needs login → business.facebook.com.
- **#791**: confirmed not API-readable — app-side only.
- **Approvals**: #784 "approved" · #794 batch size · #793 partner picks · #795 page edits.

## Session 2026-10-28 (cont.) — platform-rejection proactivity memorized

- User directive: "we must always be proactive about platform-side rejections" — codified in AGENTS.md (proactivity principle section) + company brain memory ops-platform-rejection-proactivity.
## Session 2026-10-28 leftovers
- [ ] X OAuth approve — fresh auth_token needed in .env OR click reauth_urls URL; bridge pin now holds session 600s (PR #1051)
- [ ] LinkedIn dev portal — grant "Member Data Portability API (Member)" product → li_member_snapshot.py + consent healer auto-harvests connections (PR #1046)
- [ ] WhatsApp — standard registration via POST /{phone}/register (NOT request_code/verify_code — those are migration API; 2388091 decoded)
- [ ] heal_consents hourly beat — twitter/linkedin/whatsapp/bridge probes, Slack alerts on state flips (PR #1049 + #1053)

## 2026-10-28 — residual automation round

- **IG login AUTOMATED** — `session_bootstrap.py` + `rfb-input` proven end-to-end: trusted RFB input fills email/pass, submits, gets real server response. Residual narrowed from "manual login" → "rotate `INSTAGRAM_PASSWORD` secret" (stored pass is stale; IG returns "login information incorrect"). After rotating: `python3 .devin/skills/bridge-session-ops/scripts/session_bootstrap.py instagram --bridge http://localhost:9232`.
- **rfb.py bug** — missing `import shlex` fixed.
- **wa_verify_daemon.py** running — WhatsApp fully unattended (hourly retry + ADB/mail poll + auto-verify).
- **X** — `X_WEB_AUTH_TOKEN` stale (verified via onboarding redirect); no password stored anywhere → stays physical (one x.com login on noVNC).
- **FB** — live session confirmed on shared bridge (c_user/xs extracted) — business console surface warm.
- **LinkedIn** — OAuth URL + `LINKEDIN_COOKIE` ready; bridge frees after platform lock releases.
- **TikTok** — `business.tiktok.com` logged in on adhoc browser; cert upload is the physical step.

## 2026-10-28 — residual automation ceiling (live findings)

- **IG**: proven auto-login path = `session_bootstrap.py` + `rfb-input` — RFB trusted typing fills+submits, real server response. Blocker: stored `INSTAGRAM_PASSWORD` is stale (IG rejects it). Rotate secret → rerun script → done. NOTE: RFB only works on `engagement-browser` (x11vnc :5900), which is contended — the shared bridge cycles platform sessions (skool/facebook/tiktok) and preempts mid-flow. IG `sessionid` is extracted live but never persists to `/app/cookies/` — no jar transplant path to `instagram-private-api`.
- **X**: OAuth1 (`TWITTER_ACCESS_TOKEN`) AND web cookie both dead (401 + onboarding redirect). No X password stored anywhere → stays physical. OAuth URL ready for the one login.
- **WA**: `wa_verify_daemon.py` running — fully unattended.
- **FB**: live session confirmed on bridge (c_user/xs).
- **rfb.py** gotcha: hardcodes `engagement-browser` — clicks sent to adhoc browser (9232) silently do nothing.

## Session 2026-10-28b — sidecar gate push (verified wins)

- **#784 CLOSED** — Meta e2e verified live: test post published+deleted on
  cloudless.gr page (116436681562585); subscribed_apps shows messaging webhook
  fields active on app cloudless_gr (1936126137016578).
- **#785** — Graph API: business `cloudless.gr` (1558125105019725) is APPROVED,
  WABA 1073707258453499 ACTIVE. Only leads_retrieval App Review remains
  (console-only, possibly out of scope).
- **#786** — WA console explored end-to-end via FB session on adhoc browser:
  phone Offline/NOT_VERIFIED, username @cloudless.gr RESERVED, dev console has
  0 required actions, WhatsApp Manager shows payment-method alert (no payment
  ever per policy). Cloud-API-terms accept not visible — likely bound to the
  app's WhatsApp product onboarding (app only shows FB-Login-for-Business +
  Instagram use cases) or WABA-owner identity.
- **#791** — TikTok inbox readable via cookie-injected adhoc browser:
  1 pending video share "Cloudless | Managed Cloud" (10/15) + 5 unsupported
  app-only messages (9/26). Profile view counts allow dup-check before upload.
- **#788** — DEFINITIVE: all stored IG sessionids dead (login_required /
  redirect-loop via aiograpi + direct web API); IG session transplants are
  fingerprint-bound. Only paths: password rotation (stale) or app login.
- **Bridge fixes**: cleared stale 409 lock via pkill browser-bridge;
  adhoc browser (9224) accepts /session/cookies injection + free navigate —
  session transplant works for facebook + tiktok, NOT instagram.
- **instagram-private-api**: socksio installed (httpx[socks]) + WARP routing
  via socks5://stack-ops:1080 (warp-proxy needs stack-ops wake path).
- **mail-code-fetch skill landed** (#1078) — dovecot OTP fetcher + shlex fix.

### Still physical
- #786: Cloud-API-terms accept (owner, WABA-owner identity) then register+verify_code
- #788: IG password rotation or mobile-app website edit
- #789: ΓΕΜΗ cert upload on business.tiktok.com (logged in on adhoc)
- #785: leads_retrieval review submission if still needed
- #793/#794/#795: owner replies; X login (no creds anywhere); LinkedIn org
  re-auth approve click (li_at fingerprint-bound — do NOT transplant)

## Session 2026-10-28 (cont.) — publish-kill root cause: task soft_time_limit

- LinkedIn test publish kept dying mid-flight: `process_publish_queue` had `soft_time_limit: 900` but a LinkedIn org image publish legitimately takes ~15-25min (quality gate → LanguageTool wake → image init/PUT/poll → post → D1 sync). SoftTimeLimitExceeded killed it mid-flow, stranding the row 'processing' — the "wedge" was self-inflicted. 18 kills in logs. Bumped to 2700/3000s, synced to all workers+beat, restarted. Watchdog stale-lock 15→45min to match.

## 2026-10-28 — LinkedIn CM-API upgrade submitted + watchers

- [x] CM-API Standard Tier form filled + submitted via adhoc bridge (app `264925843`) — 100% completion confirmed
- [x] Screen recording: Playwright walkthrough → mp4 → Nextcloud public share `cloud.cloudless.gr/s/WxFDFGgwnZJSxtd`
- [x] Reviewer account `linkedin-review@cloudless.gr` (editor, main team) created for app review
- [x] `devapp_mail_watch` (gmail-ops) — daily 09:05 cron watches Gmail for Microsoft Vetting / LinkedIn review mail → Telegram
- [x] `qualtrics-ops` skill — QID-keyed Qualtrics fill + `.select-menu` dropdown support
- [ ] **Open:** OAuth uses client `77tf4oysp8u3fz` ≠ submitted app (`77j8kzfi1a6jn8`). If it's a second app, either submit its upgrade form (needs its Products-tab link) or switch `.env` creds to the approved app once Standard Tier lands.
- [ ] Watch `baltzakis.themis@gmail.com` for Microsoft Vetting Services verification email

## Session 2026-10-28c — master green-out
- Backend CI GREEN (run 40707100096): all unit tests + lint + coverage gates.
- Landed: #1079 synigoros_status task module (missing beat dep, 5 CI fails),
  #1080 synigoros-ops skill, #1078 mail-code-fetch skill,
  #1082/#1083/#1085/#1093 coverage tests (synigoros, task tails, _probe
  branches, consent_healers tails) — restored 99.0/95.3 gates,
  #1084/#1087/#1088/#1090/#1092 lint fixes (parallel-merge leaks).
- #1073 closed — corrupted rebase (2,604-file deletion); synigoros skill
  salvaged separately.

## Session 2026-10-28 (cont.) — LinkedIn post copy discrepancy resolved

- LI post published with template copy (my draft insert carried the cloned row's content_text, not the drafted "cognitive drift" copy). Published version is on-topic + live at urn:li:share:7521213608551292928 — LEFT LIVE (deleting would trail + double-fire for marginal gain).
- Drafted LI copy survives in the quality-gate version log (13:13Z insert) — reuse as a FOLLOW-UP post instead of correcting the live one.
- FB/IG/Threads carry correct drafted copy — publish as-written.

## Session 2026-10-28 (cont.) — human-gate automation deep-dive results

- AUTOMATED chain proven for LinkedIn OAuth re-auth: secret_store has LINKEDIN_PASSWORD → POST /login on sidecar:9225 → 2FA required → fetch-code.py reads code from cloudless.gr mail (got 766720) → retry with verification_code.
- BLOCKER hit: after first failed code retry, LinkedIn escalated to a challengesV2 page with NO code input (device-trust challenge — needs approval in the LinkedIn mobile app or a trusted-device session). This is the same gate the Slack alert flagged. Human: approve the sign-in in the LI app ONCE → device becomes trusted → future re-auths automate via the mail-code chain.
- X: TWITTER_LOGIN_PASSWORD + X_WEB_AUTH_TOKEN exist in secret_store → bridge x.com login + seed-x fully automatable (not yet run).
- TikTok cert: ~/Downloads/cert.pdf found; sidecar /business-verification/fill automates fields; upload+submit is manual-by-design in sidecar code.
- WhatsApp: wa_verify_daemon fully automated, ADB reads SMS; rate-limited until 02:30Z.
- Sidecars expose /debug/navigate + /debug/eval — generic browser-drive surface (LinkedIn 9225, TikTok 9224, FB 9226, bridge 9223 w/ 'expression' field).

## 2026-10-29 — gate-capture + disk-heal + billing-guard architecture

**human-gate-capture-ops** (new) — every residual human input terminates at
`gate_capture.py` (15-min cron):
- **creds**: `bsky/viber/whatsapp/meta <secret>` via Telegram msg to the bot,
  or `*.txt` in Downloads → provisioned + file shredded
- **sessions**: `*_harvest.py` pulls live browser creds via CDP
  (bsky.app→app-pw mint, webadmin.viber.com→token, business.facebook.com→sysuser token)
- **probes**: `_waba_watch` polls payment state → pings on change
- **floor tools**: `viber_phone_capture.py` (ADB uiautomator→token),
  `meta_payment_helper.py` (CDP billing fill, env-only card, submit stays manual)

**consent-root-ops** additions — `cdp_ensure.py` (multi-path :9222 launcher),
`install-chromedebug-task.ps1` (current-user logon task, no admin).
start-chrome-debug.bat already in Windows Startup.

**cf-billing-ops** — `observability_guard.py` (daily): forces obs off/5% on
all workers, pings cancel-steps Nov 20→Dec 1, `obs_cancel.py` auto-clicks
the dashboard cancel via CDP at T-4d, `ack` silences.

**omv sda1 disk** — `omv-disk-prune.yml`: diagnose_only audit (proved k3s
innocent — real consumer: docker-cache blobs 20G, NAS-Backup 11G, runner-work
7G), targeted prunes reclaimed 94G→69G (82%→60%), weekly Sun-04:20 auto-heal.

**Bluesky** — `bsky_claim_handle.py` claimed `cloudless.gr` on the wired
account via _atproto DNS verification (no password needed).

**Audit auto-booking** — `datalake_executor.audit_escalation` auto-emails
requesters the self-book link (chatbot's cal APIs); per-email audit_invite
dedup. Cal.com→google-calendar fallback merged (#2316).
