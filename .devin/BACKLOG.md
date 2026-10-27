# BACKLOG (living)

## Done 2026-10-27 (PR sweep — umbrella merge)
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
