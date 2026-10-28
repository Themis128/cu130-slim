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

## 2026-10-27 — Session closeout (post-sweep)

- Landed: account-status-reconcile (#986→#993) and nurture-sidecar-warmup (#987→#990); BACKLOG registrations merged (#988). Both LTE issues closed.
- cloudless.gr: umbrella #2305 landed + subsets closed; dependabot groups merged; #2308 skills+CI-path fix; #2309 links page (kept restored GSC page — PR was reverting); #2310 AI-governance offer draft live on /services.
- Owner-gated remains (status comments + proposals posted on each): #784 test-post approval, #793 partner approvals, #794 batch size, #795 offer edits, #785 Meta evidence, #786 WA SMS (rate-limited — owner-side request best), #788 IG session bootstrap via noVNC, #789 TikTok captcha at noVNC (browser staged), #791 app-side share review.
- New: espo_leads_tool.py CLI; owner-gate-ops + pr-sweep-ops skills.

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

## Session 2026-10-28 (cont.) — PR/draft auto-healers

- New `scripts/pr-watchdog.sh` (hourly task `Cloudless-PRWatchdog`, headless): per open PR on cu130-slim + cloudless.gr — failed checks→alert, checks pending→skip, green+clean+auto-merge-armed→squash merge fallback, behind→update-branch, blocked→alert with unresolved-thread count. NEVER auto-resolves review threads or merges unarmed PRs.
- compose-watchdog check #6: drafts >24h with empty media_ids → Slack alert (no auto-attach — correct-media rule needs judgment).
- PR #1058 merged to master. PR #2312 blocked on a real Sentry catch (single-asterisk italics don't render) — fixed to `**`, thread resolved, auto-merge re-armed.
- feat/gmail-ops-v2 reconciled with origin (picked up li_member_snapshot). 4 recap drafts now carry brand media; needs_media flags cleared.

## Session 2026-10-28 (cont.) — recap drafts reviewed + scheduled

- Reviewed all 4 shift-forward drafts: on-brand, no invented metrics, business accounts only.
- Added UTM links (one campaign slug `shift-forward-2026`, utm_medium=social, per-platform source → /contact audit CTA) — closes the UTM coverage gap on these posts.
- Scheduled staggered by tier: LinkedIn Thu 07:00Z, FB Thu 11:00Z (its peak window), IG Fri 08:00Z, Threads Fri 12:00Z.
- GOTCHA learned: posts.scheduled_at alone does NOT publish — publisher scans `publish_queue` rows (process_publish_queue). Enqueued all 4 (priority 5, pending).
- Watchdog bug found+fixed on first live run: `local key=... f=...$key` unbound under set -u in both watchdog scripts.
