# BACKLOG (living)

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
