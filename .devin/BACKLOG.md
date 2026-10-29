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

- **facebook-browser-sidecar wedged on profile-picker** — Docker shows
  Up+healthy but the daemon livelocks clicking "Continue as"; every
  /debug/* and /session/validate call times out. Heal per session-ops:
  full session re-seed (or browser-novnc noVNC manual pick). Until then
  meta_appeal_form.py + wa_onboard_retry.py can't run (both drive 9226).
- **ntfy phone DONE** — subs live on 192.168.1.200:30080 (LAN-direct, no
  VPN/CF needed) with notifications flowing; battery/hibernation exempted.
  Optional: add `phone`/`Oabmv3U4vCtDXbeOaDns` for ntfy-ws.cloudless.gr
  in Manage users for off-home-WiFi delivery.
- **Meta appeal** — entry = facebook.com/help/contact/2026068680760273
  (disabled-account appeal form; meta_ads_appeal_form.py inventories
  fields). Geo fix = the unusual-activity trigger. One-shot unblock for
  ads + finance_permission + whatsapp-onboarding-block.

## Leftovers (human gates)
- X re-auth: click x-reconnect.sh output URL (works — verified live)
- LinkedIn password + li_at rotation (exposed earlier in session)
- Community Management API: dedicated 2nd app dev-portal submission
- 2 live posts link cloudless.com not .gr — delete+recreate awaiting go-ahead (fb 7c7948a4, li bd301640)
- Threads business profile: browser-session edits (bio/name) — threads-ops scripts ready
- Stacked-branch conflicts recur — prefer fresh-off-master topic branches

## 2026-10-27 — PR #948 review follow-ups (branch fix/pr-948-viral-meta-forwarding)
- dispatch worker now forwards `c.meta_data` (viral flag) into service candidate — fixes lost duet_flag gating
- test_dispatch_full_sweep: fixed stale meta_data assertion + added missing gated-items fake result (slack 4→2)
- _Res.scalar() added for daily-cap count query; new test_dispatch_viral_tiktok_becomes_gated_duet_flag (ingest→dispatch)
- 49 engagement unit tests green in social-api container; pushed as branch (PR #948 head lives on bot fork — merge/cherry-pick needed)
