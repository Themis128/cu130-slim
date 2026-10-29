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

## 2026-10-27 — PR #948 review follow-ups (branch fix/pr-948-viral-meta-forwarding)
- dispatch worker now forwards `c.meta_data` (viral flag) into service candidate — fixes lost duet_flag gating
- test_dispatch_full_sweep: fixed stale meta_data assertion + added missing gated-items fake result (slack 4→2)
- _Res.scalar() added for daily-cap count query; new test_dispatch_viral_tiktok_becomes_gated_duet_flag (ingest→dispatch)
- 49 engagement unit tests green in social-api container; pushed as branch (PR #948 head lives on bot fork — merge/cherry-pick needed)

## 2026-10-29 — phone/gates wiring session
- TikTok #789: business verification RESUBMITTED — "Under Review" (~3 business days).
  Clean ΑΑΔΕ cert (ΑΦΜ 115307716) uploaded via Business Suite → Verify → Edit → Submit;
  prior rejection was name/doc-type mismatch. phone-ops skills landed (#1132/#1135/#1137/#1139/#1140).
- ntfy phone app WIRED: subscribed cloudless-alerts + cloudless-ops + nextcloud-ops on
  http://192.168.1.200:30080 (k3s NodePort, LAN path — no Tailscale dep). `phone` pw rotated;
  stale ntfy-ws sub deleted. Learnings PR #1147. Server users/ACLs verified via ntfy-ops.
- Windows bridge live: win_agent at http://100.83.120.93:8765 (Startup launcher staged —
  survives logon). Used for adb tcpip 5555 + debug-Edge launch. CDP forwarder
  http://100.83.120.93:9223 → localhost:9222 (cdp_fwd.py running via agent).
- Debug Edge open on desktop with 3 gate tabs: WA terms (#786), x.com login, social.cloudless.gr.

## Leftovers (human gates) — updated 10-29
- ONE interactive login in the open debug Edge unlocks: #786 WA terms, #785 Meta evidence,
  X OAuth. After X login: run totp-ops totp_setup.py to enroll TOTP → future re-auths unattended.
- #788: INSTAGRAM_PASSWORD empty in .env — set it, then session_bootstrap.py runs.
- #793/#794/#795: reply approve/done (owner-gate pipeline consumes on 6h beat).
- Phone: wireless-debugging toggle persists; tcpip 5555 does NOT survive reboot (win_agent redoes it).
- ntfy phone must NOT be force-stopped (re-enables itself anyway; kills nothing critical).
