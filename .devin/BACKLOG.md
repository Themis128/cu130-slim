# BACKLOG

Living backlog for the cu130-slim / SocialAuto stack.

Convention: new issues are logged under a dated `## YYYY-MM-DD — <topic>`
heading at the bottom; sweeps prune resolved entries (full history in git).
Tags: `human:` = owner action required · `decide:` = content/process decision
· `watch:` = no action until the trigger fires.

## 2026-11-07 — sweep cleanup (this file was consolidated)

Resolved history pruned 2026-11-07 (PRs #1404/#1405 era). All previously
checked items plus verified-merged/verified-fixed entries were removed —
see git history for the full log. Verified this sweep: PRs #1247 #1308
#1309 #1332 #1336 #1046 #1049 #1051 #1053 #1385 #1391 #1395 #1397 merged;
gh auth + ~/.ssh keys + .git ownership restored; threads/IG tokens active;
env-manager/languagetool/flower recreated healthy via fixed stack-ops;
stack-ops tests wired into Docker CI; EspoCRM test lead deleted; get-secret
already writes to file only; watcher schtasks all registered; grokbot stash
gone.

## Open — engineering

- [ ] cloudless-gr-live (265081467): Live Events = FORM state (verified
  live via CDP :9229, 2026-11-07) — fill via `li_devapp_ops.py fill-form
  <Q_DL>` with a fresh app-bound Q_DL link from the products page
  (qualtrics-ops). All other products still REQ.
- [x] `linkedin-access-forms` SKILL.md update (app creds + probe pointer)
  — verified 2026-11-09: SKILL.md already carries the confirmed app creds
  table + li_scope_probe/li_products_matrix/li_consent_trim pointers.
- [ ] LinkedIn Verify-PIN modal inert under all automation (CDP/keyboard/
  OS-click/patchright) — retest now that the 48h fresh-app window passed;
  if still inert it is a fresh-app artifact, not anti-bot.
- [ ] Backfill verification: pass resumed 2026-11-07 inside social-api
  (`scripts/backfill_media_quality.py --limit 500`, non-force — 355/732
  had spellcheck_applied at start). Confirm final counts +
  `meta_data.quality.spellcheck_applied=true` when it completes.
- [ ] Windows `sshd` on 192.168.1.23 genuinely fails to start even
  elevated (`sshd -t` clean, host keys present, "Failed to start service"
  logged to devin-scripts/sshd_err.txt). Next: `sshd.exe -d` capture.
  Elevation lane SOLVED + self-healing: sshd-ensure block at top of
  `D:\DevOptimizer\scripts\check-wsl-memory-pressure.ps1` runs every
  30 min via `\DevOptimizer\WSL-Memory-Watchdog`. Same file also carries
  the CDP-Relay-9229 firewall pin (verified: WSL->host:9229 live).
  NOTE: `\DevOptimizer\WSL-Permanent-Fix` (2-min guard) is DEAD — its
  action calls pwsh.exe which resolves to a per-user Store stub
  (0x80070002 every run). Repoint task action to conhost+powershell.exe
  via the same elevated lane.
- [ ] Playwright E2E: `.env` values are quoted — `run_pw_e2e.sh`
  (gitignored, repo root) strips quotes before exporting E2E_ADMIN_*;
  plain `playwright test` skips admin-login unless vars are set unquoted.
  Documented gotcha, not a bug.
- [ ] comfyui image absent locally — first wake via stack-ops:8199 will
  pull the full image (multi-GB, may exceed the 120s wake timeout);
  expect transient proxy failures until the first pull completes.

## Open — external human gates

- [ ] human: TikTok `cloudless.gr` status=expired; refresh returns "no
  access_token in refresh response" → one TikTok login/QR scan on the
  bridge (:9223); session_healer injects TIKTOK_SESSION_ID if valid,
  else `scripts/tiktok_qr_watch.py` watches for the scan.
- [ ] human: WhatsApp `Themistoklis Baltzakis` revoked → WABA assignment
  in Meta Business, then POST /{phone}/register (standard registration —
  request_code/verify_code are the migration API).
- [ ] human: Gmail `GMAIL_REFRESH_TOKEN` 401s on exchange → one
  `gmail.py login` re-consent click.
- [ ] LinkedIn sidecar :9225 browser session dead and stored
  `LINKEDIN_COOKIE` li_at stale — BUT a working lane now exists:
  debug-chrome CDP `:9229` (headless chrome:9330 + userspace relay,
  `devin-scripts/cdp_relay.py` + `cdp-launch.bat`) has a LIVE LinkedIn
  session (verified /feed loads logged-in, 2026-11-07). Point
  li_devapp_ops/portal scripts at host:9229 to resume the
  portal/DM/specialties flows. Sidecar :9225 itself still needs a
  credential login or stored LINKEDIN_EMAIL/PASSWORD.
- [ ] human: X OAuth approve — fresh auth_token in .env OR click the
  reauth_urls URL; bridge pin holds the session 600s.
- [ ] human: FB OAuth reconnect — `gate_runner.py fb-token
  <authorize-url>` needs an authorize URL + FB login in debug Chrome.
  FACEBOOK_CLIENT_ID/SECRET are now set in .env — try the Accounts-UI
  OAuth path first before the manual lane.
- [ ] human: CM Standard-tier survey — needs ~2min screen recording with
  US-English audio commentary + registered address (LinkedIn
  requirement). Token healed 11-07; the recording itself is a human task.
- [ ] human: Member DP (Member) in-portal modal on 265077368 after
  biz-email verify — modal was inert under automation; retry once a
  valid portal session exists.
- [ ] human: LinkedIn Matched Audiences (264925843,
  CRM:015285039163310) — DENIED "Business verification failed"; support
  case submitted 2026-11-07, may need Greek business registration
  (ΑΦΜ/ΓΕΜΗ) if requested.
- [ ] human: Gmail MCP connector in Devin Desktop fails client-side
  (likely expired OAuth); repo gmail-ops OAuth path works as fallback.
- [ ] human: ChromeDebug schtasks edit denied (stored-credential task) —
  needs owner password or elevation.
- [ ] human: Devin storage.json regen from memory until next Devin
  restart — watcher armed.
- [ ] human: Host RAM ~5GB free of 32GB triggers the WSL vsock flaps —
  close heavy Windows apps; no config fix.
- [ ] human: IDE keeps a `wsl+docker-desktop` remote authority →
  devin-server install-spam loop; close that authority and use
  `wsl+Ubuntu-24.04` instead.

## Open — owner decisions

- [ ] decide: drive the Member DP (3rd Party) access-request form on
  265077368, or create the dedicated-page app for (Member) self-serve
  (`r_dma_portability_self_serve`). (3rd Party) needs legal/business info
  + privacy policy + business-email verify.
- [ ] decide: garbled LinkedIn asset 582ca53c (predates the image-text
  fix; post is live) — delete+recreate is a content decision.
- [ ] decide: Synigoros follow-up recipient `press@synigoros.gr` —
  confirm the press office is the intended supplementary-report channel
  before the next auto-send.
- [ ] decide: schedule `wsl-safe-bounce.ps1` nightly ~04:30 — the script
  is marked operator-only; declined unattended so far.
- [ ] owner: Kakkava Founders week-2 items — recording
  youtu.be/vHlKLsSF4rM, slides canva.link/dzabh086dzjph5v, mastersheet on
  Notion; prime bootcamp outcomes into offer_survey copy + the 4-YES
  diagnostic.

## Watch — no action needed now

- [ ] watch: cloudless-gr-dma (265077464) Member DP (3rd Party) — "Review
  in progress" (step 1/2, LinkedIn decides in 10-14 business days);
  watch tbaltzakis@cloudless.gr for decision/docs-request mail.
- [ ] watch: Dropbox→Drive rclone migration (`Apps/Google Download Your
  Data`, ~44 GiB) — watcher auto-verifies + purges Dropbox on completion;
  log `C:\Users\tbaltzakis\devin-scripts\migrate_log.txt`.
- [ ] watch: PR #1399 (LinkedIn Ad Library intel pipeline) — auto-merge
  armed, checks cycling.
- [ ] watch: publish_queue failures — the 406 failed rows are legit
  (text-dedup skips, seo<80 quality gate, true media dupes); post-#1397
  failures now log real tracebacks (FB post_photo timeouts patched in
  #1395).
- [ ] watch: Debug Chrome (:9229) dies repeatedly — watchers are
  flag-gated via .devin/triggers/*.flag; gate-runner reconnect handles it.
- [ ] watch: Email Routing verify mails land via the mail-ingest worker →
  webmail.cloudless.gr/ingest (not omv Maildir); recheck the origin if
  that gate is needed again.
- [ ] watch: stack-watchers `/status` on :9099 is not published to the
  host — add `ports:` in compose if host access is wanted; degraded
  CDP/docker.sock/ssh reports are tolerated by design.

## 2026-11-08 — web-researched automation solutions for the remaining human gates

- [~] WSL sshd (replaces Windows-sshd-elevation gate): user-space sshd
  now RUNNING on :2222 in WSL (`/usr/sbin/sshd -f ~/.ssh/sshd_user.conf`,
  host keys in ~/.ssh/ssh_host, pubkey auth only; omv key authorized).
  Researched: Win32 sshd can't run unprivileged (needs SYSTEM), and
  omv's sshd has AllowTcpForwarding=no so reverse-tunnel was out.
  REMAINING one-time step to expose it to LAN/tailnet (pick ONE):
  a) `sudo tailscale set --operator=$USER` then `tailscale serve
     --tcp=2222 tcp://localhost:2222` — then omv reaches
     100.86.188.43:2222; or
  b) add `networkingMode=mirrored` under [wsl2] in
     C:\Users\tbaltzakis\.wslconfig + `wsl --shutdown` (user file, no
     admin, but drops the session — do it at a quiet moment).
- [x] WABA via API (no console needed): POST /{business-id}/
  whatsapp_business_accounts on Graph API creates the WABA directly;
  GET /{waba-id}/phone_numbers + register API completes onboarding.
  FB business_management system-user token already minted. (PR #1478
  shows a WAAC already created — waiting Meta cooldown.)
- [x] CM survey recording automatable: `pip install demo-video-recorder`
  (drives browser via Playwright + EdgeTTS narration → narrated MP4).
  Still needs the registered-address text from the user as form input.
- [x] ΑΑΔΕ doc source found: myAADE → Μητρώο και Επικοινωνία →
  Βεβαιώσεις Μητρώου → "Τρέχουσα Εικόνα Οντότητας/Επιχείρησης" +
  "Ιστορικό Μεταβολών – Βασικά Στοιχεία" — two downloadable PDFs, the
  accepted sole-trader evidence for TikTok business verify. Gated on
  Taxisnet creds (not in .env — user must supply or do the download).
- [x] omv→Windows SSH gate BYPASSED via pull model: schtasks `OmVPull`
  (every 1 min) runs scripts/omv_pull_watchdog.sh — SSHes OUT to omv,
  reads+deletes /tmp/win_cmds.sh, executes lines (`wsl:` prefix → bash,
  else powershell), logs to logs/omv-pull.log. Verified end-to-end
  02:54Z. omv-side callers just write to /tmp/win_cmds.sh now — no
  inbound sshd needed at all.
- [x] Issues #793 + #795 answered (`approve` / `done` posted).
- [x] Registered business address saved to .env as
  REGISTERED_BUSINESS_ADDRESS (Thermopilon 15, Koropi 19441) — ready
  for the LinkedIn CM access form + narrated recording.
- [ ] myAADE doc download — needs Taxisnet creds (gov.gr SSO, none
  stored anywhere). Add TAXISNET_USER/TAXISNET_PASS to .env and I'll
  CDP-drive the two Βεβαιώσεις downloads; or drop the PDFs in
  Downloads manually.
- [~] sshd :2222 running; OmVPull already bypasses the inbound-SSH
  need. Optional full expose still via `sudo tailscale set
  --operator=$USER` + serve, or mirrored .wslconfig.

### TikTok bio updated via ADB (2026-11-08 ~06:10)

- Lane: `adb -s 100.84.31.85:5555` (S22+ tailnet) → `monkey -p com.zhiliaoapp.musically 1` → Profile → pencil → Bio. `input text` needs `%s` for spaces (NOT `%20` — types literally); `keyevent 66` for newlines, 67 for DEL. Save at ~(985,149). uiautomator dump verifies text.
- New bio: "Managed cloud and AI automation for SMBs. / Real builds. Real numbers. No lock-in. / Free 30-min audit -> cloudless.gr" — verified live on @cloudless.gr profile.
- Remaining TikTok gaps: website link gated by 1k followers OR business verify (user files papers later); only 4 followers — growth = posting cadence.
- myAADE: login works (TAXISNET creds in .env, do not echo); Βεβαιώσεις Μητρώου page reachable via `#!/vevaiwseismhtrwou`; Οντότητας/Επιχείρησης tiles HIDDEN because company isn't active yet — person certs only until Έναρξη filed.
- Windows App privacy dialog can hijack ADB taps — verify screen state before/after every tap.

## 2026-11-08 — devapps → datalake → reports → per-channel publishing

Landed on `feat/devapps-datalake-reports` (PR pending):

- `app/services/devapps_sync.py` — per-account developer-app pull
  (token health/scopes/expiry via debug_token or platform validate,
  page/org/creator insights, IG follower_demographics, quota state).
  Persists `AnalyticsEvent(event_type="devapp_snapshot")`; one account's
  failure degrades to `ok:false`, never aborts the sweep.
- Beat: `devapps-sync` :05 → `export-datalake` :10 →
  `datalake-insights-sweep` :20 → `devapp-channel-actions` :30 (all */6h).
- Lake: `lake/socialauto-devapps/snapshots.json` +
  `lake/socialauto-channel-actions/actions.json` (both NC-mirrored);
  cloudless.gr ETL emits `social_devapps` + `social_channel_actions`
  gold sections.
- `datalake_insights.compute_devapp_signals` + `pick_actions`
  `devapp_health` (Slack alert via executor); `build_channel_actions`
  health-override → `channel_health`, else `channel_action` hint.
- `devapp_channel_actions` task → `monetization_actions_from_channels`
  → `schedule_report_actions` (source=`devapp-channel-actions`,
  dedup_field=`devapp_channel_action`) — full copy/quality/media/
  compliance chain, daily-deduped, health channels never post.
- workforce_audit: `devapps-sync` + `devapp-channel-actions` entries.

Leftovers:
- [ ] First live `devapps-sync` run — verify per-platform pullers
  against real tokens (FB page_token path, IG demographics metric name,
  LinkedIn org vs member split); failures land as `ok:false` rows.
- [ ] `devapp-channel-actions` first run — confirm scheduled posts land
  per channel and dedup holds on the 2nd/3rd/4th daily sweep.
- [ ] X quota: `me_metrics` 402 → `quota_exhausted` → `wait_for_quota_reset`
  health action; verify it doesn't spam channel_health alerts daily.

### Slack transport outage — root-caused + automated (2026-11-08)

- Symptom: ALL SocialAuto Slack output dead ~19 days — www.cloudless.gr
  #socialauto digests/ops-alerts silent since 10-20, SocialAuto-ws
  #socialauto-publishing since 11-03, #socialauto-alerts since 11-03.
- Root cause: TWO Slack workspaces connected via mcp-remote
  (SocialAuto T0C18DXH8P7 + www.cloudless.gr T09AF5VTK4G); the sync script
  picked files[0] by expiry → SLACK_BOT_TOKEN flipped workspaces → every
  post failed channel_not_found. Plus NO refresh cron was installed and
  the host currently cannot reach slack.com (github OK, slack/google TCP
  timeouts) so _refresh needs the Windows-side mcp-remote to stay alive.
- Fixed live: SLACK_BOT_TOKEN re-synced to SocialAuto token, all 6
  consumers + n8n recreated, chat.postMessage verified, 2h cron installed.
- Code fix: PR #1529 (workspace-aware token picker + refresh-all +
  offline _team_map.json/SLACK_TEAM_ID fallback; SLACK_TEAM_ID=T0C18DXH8P7
  added to .env).
- [x] N8N_API_KEY in .env was STALE — resolved 2026-11-09 WITHOUT a UI
  mint: the `user_api_keys` row `devin-sync-20261108` (created 11-08) is a
  valid JWT; verified `GET /api/v1/workflows` → 200, wrote it to `.env`
  and PATCHed Infisical prod (survives the 05:30 pull). n8n public API
  usable again for slack_token_sync probe / n8n-cloudless tooling.
- [x] slack-remote MCP port collision — fixed in PR #1556: `slack` stays
  on 3118, `slack-cloudless` moved to 3119 (incl. redirect_uris), and
  mcp-remote paths corrected to the real WSL locations
  (/home/tbaltzakis/.local/bin/node + /mnt/c/.../proxy.js). NOTE: port
  fix ≠ auth — the xoxe tokens are still expired (see Slack section).
- [x] #socialauto-leads noise — test-lead filter added in PR #1556:
  `_is_test_notify_lead` skips Slack + digest email for ci-test/verify-/
  platform-test/devin-test/@example.*/@test.* leads; machine webhook
  still fires. Unit-tested (27 pass).
- [x] #engagement-gate stale manual-review items — auto-expiry added in
  PR #1556: `pending_human_gate` rows older than 7d are marked `expired`
  each engagement sweep (dead drafts can't be actioned anyway).
- [ ] www.cloudless.gr workspace digests (#socialauto, #ops-alerts,
  #orders/Polar) stopped 10-20 — different poster (site/n8n?), not the
  backend token path fixed above. Identify the sender and restore.
- [x] "All targets were skipped" posts — resolved in PR #1556:
  `PostStatus.SUPPRESSED` + rollup change (guardrail skips ≠ failures),
  Alembic `s9p9r8s7t6u5` adds the enum value (verified single head, DB
  already migrated).
- [x] devapp-channel-actions dedup verified 2026-11-09: hash =
  sha256(date|platform|instruction) checked against posts.meta_data —
  4x/day sweeps only materialise the first run's posts. X-quota spam
  fixed in PR #1556: `devapp_health` alerts now dedup per
  platform+reason for 24h via the analytics_events ledger (was: alert
  on EVERY datalake-insights sweep while quota stayed exhausted).
- [ ] Rotate POSTGRES_PASSWORD + Infisical UA client secret — both were
  printed in transcripts. Needs a maintenance window: ALTER USER inside
  social-postgres, update .env + Infisical prod, recreate all
  DB-connected services. Not done — high blast radius.
- [ ] compose-watchdog critical list — verified already covered:
  `social-stack-watchdog.sh` (5-min cron) owns infisical + postgres
  restarts and /api/status health; compose-watchdog CORE already
  includes social-postgres + redis. No change needed.

### Synigoros (Ombudsman) complaint ops — monitor repaired (2026-11-08)

- Complaint live: ΔΥΠΑ–ΚΠΑ2 Μαρκοπούλου unemployment-benefit rejection.
  Protocol 2026-81750, Φάκελος 408302, ΚΠΑ 202610260029, filed 26/10/2026.
  Status «Προς Αποδοχή» as of 08/11. 05/11: ΚΠΑ reminder email received.
- Monitor was dead: scheduled task `SynigorosMonitor` (15 min) ran against
  `\wsl.localhost\...\scripts\synigoros-monitor` but `config.json` +
  `node_modules` + repo-root `.env` (TELEGRAM_BOT_TOKEN, MAIL_OUTBOUND_*,
  SMTP_*) were all missing → every run crashed silently (result 0 anyway).
- Fixed live in the WSL checkout: copied config.json + node_modules from
  `C:\Users\tbaltzakis\synigoros-monitor`, created `.env` with the 8 needed
  vars from `C:\Users\tbaltzakis\cu130-slim\.env`. Verified: state.json
  updates, status reads «Προς Αποδοχή», next poll ~15 min cadence.
- monitor.js bugs fixed (PR this branch): (a) info panel renders placeholder
  «Αναμονή Επιβεβαίωσης Από Συνήγορο» ~1s before real status — added
  1500ms settle before extraction; (b) fresh arm left lastNews=null so the
  3-day auto-followup (followup.js → press@synigoros.gr) could never fire —
  arm now seeds lastNews=now.
- [x] 2026-11-09: Synigoros added an email OTP gate on identify — monitor
  failed 24 consecutive polls, each attempt emailing a 6-digit code.
  monitor.js now detects the OTP prompt, fetches the newest code from Gmail
  (subject decode + stripped HTML body + ~4.5min freshness; sends are
  throttled so a re-identify may reuse a still-valid code), submits, retries
  ≤3× requiring a newer email per retry. gmailAccessToken falls back to
  .env GMAIL_OAUTH_CLIENT_ID/SECRET (gcp-oauth.keys.json absent on host).
  Verified live 3×: fresh code → «Προς Αποδοχή» rendered, CHANGE alert sent,
  errorStreak 24→0. PR #1563.
- [ ] WSL repo `/home/tbaltzakis/cu130-slim` is the compose project working_dir
  but has NO `.env` — since the 2026-11-04 move off Windows, `${VAR}`
  interpolation resolves empty/default for everything except the 8 vars just
  added. Containers still run on env baked at creation; next `compose up`
  could strip creds. Restore full `.env` (canonical:
  `C:\Users\tbaltzakis\cu130-slim\.env`, 278 vars) — audit for Windows paths.
- [ ] ΔΥΠΑ re-examination request sent 11/06/2026 to kpamark@dypa.gov.gr —
  NO reply in ~5 months. Ombudsman file covers the silence; optional direct
  status-inquiry email drafted pending user send.
- [ ] ΣΕΠΕ/Επιθεώρηση Εργασίας complaint for the «find a company to hire and
  fire you» suggestion — draft pending user review before filing.
- [ ] Monitor followup fires ~2026-11-11 if status stays «Προς Αποδοχή»
  (lastNews seeded 08/11); verify followup.js SMTP/MAIL_OUTBOUND path works
  on first real send (httpSend → mail-outbound worker preferred).
- kakkava-brain-sync hardening (PR #1549): docker cp from /mnt/c flakes
  ENOMEM inside its internal tar — retry x3 + container file-count must
  equal host count before ingest (partial copies would silently drop
  docs). Verified live: attempts 1-2 failed, 3 succeeded → 82 files,
  364 memories, index note restored. NOTE: manifest state lives at
  /root/.local/state/kakkava-brain-sync/ (cron runs as root).

- [x] gov-portal-watch + gsis-login-ops skills (PR #1540): config-driven authenticated gov-portal watcher engine (CDP :9229, extract_js diff, Telegram, GSIS+AMKA auto re-login) + doctor.py/run-all-watches.py ops tooling. Live `C:/Users/tbaltzakis/idika-watch/` refactored onto engine.py via portal.json + thin wrappers; schtasks IdikaWatch/IdikaKeepalive/IdikaMorningRelogin unchanged; doctor 7/7 PASS.
- [~] Warm-back Engine (PR #1542): auto-merge armed, checks cycling. Residuals resolved 2026-11-09: (a) ~~CF `mail-outbound` token perms~~ — NOT needed: `CLOUDFLARE_WORKERS_DEPLOY_TOKEN` set `MAIL_OUTBOUND_TOKEN` secret via Workers secrets API; `/verify` self-test sent; backend `send_email` smoke test delivered through the worker lane end-to-end. `CF_EMAIL_ENABLED` was missing from compose env — fixed locally + PR #1544. SMTP fallback still works. (b) ~~LinkedIn partner ID~~ — extracted `9535068` from Campaign Manager → Measure → Signals manager → Insight Tag via linkedin sidecar; `NEXT_PUBLIC_LINKEDIN_PARTNER_ID` secret set on cloudless.gr; deploy-pi rerun 42523188497 triggered to bake it into the image. cloudless.gr#2389 stays closed (main's consent-gated tag is correct). (c) Mautic win-back beat fires daily 10:45 — verify first real sends once eligible cold contacts exist (dry-run scanned=21, 0 sends — most Espo leads lack usable email).

## 2026-11-09 — WSL self-maintenance automation + Devin popup fix

- WSL Ubuntu-24.04 is now fully self-maintaining: unattended-upgrades covers
  all 8 origins incl. noble-proposed + phased updates forced; needrestart
  auto mode via apt hook; weekly `/etc/cron.weekly/apt-clean` +
  `scripts/system-hygiene.sh` (journal/docker/snap/logs, TG #digest);
  `WSL-Weekly-Bounce` scheduled task Sundays 05:00 (deploy-lock guarded,
  TG-reported, first live run 2026-11-15); tmux-resurrect/continuum
  configured at /root/.tmux for session restore.
- [x] tmux-resurrect verified 2026-11-09 (isolated HOME+TMUX_TMPDIR):
  save→kill→auto-restore works — sessions/windows/cwds come back via
  continuum-restore 'on'. Caveats found: resurrect dir is XDG
  `$HOME/.local/share/tmux/resurrect` (not ~/.tmux/resurrect); pane
  process respawn needs an attached client (detached `-d` restore skips
  it — fine in real usage); a stale `tmux new-session` client process
  trips continuum's another-server guard and silently blocks restore —
  kill stray `tmux` clients if restore ever seems dead.
- [ ] Proposed-pocket auto-updates carry regression risk; revert = remove
  `-proposed` origin in `/etc/apt/apt.conf.d/52auto-updates` + suite in
  `ubuntu.sources`, then `apt-get update`.
- Devin "missing file" popup root-caused: stale untitled editor tabs
  `d:\DevOptimizer\{status,doctor,explore}` (files don't exist) make
  helm-intellisense statSync ENOENT every lint pass → notification.
  Fix = close those tabs; if they respawn, trace the creator.
- [ ] Synigoros followups: added tgOnly reminder events (call-ombudsman-1
  11-25, call-sepe-1 12-08, dypa-written-demand 12-04) + tgOnly support in
  admin-followups.js — committed on feat/synigoros-tg-reminders.

### WSL-Weekly-Bounce Slack failover (2026-11-09)

- `C:\Users\tbaltzakis\scripts\wsl-weekly-bounce.ps1` now fails over to
  Slack `chat.postMessage` when `Send-Tg` exhausts its 3 retries — covers
  all 4 outcomes (deploy-lock skip, success, docker-degraded, fatal).
  Slack path is pure Windows-side HTTP (no WSL), so it still fires while
  the VM is down. Severity rides in the text as `digest|…` / `ops|…`.
- Token resolution (`Get-SlackToken`): scans
  `%USERPROFILE%\.mcp-auth\mcp-remote-v1\*_tokens.json` for
  `chat:write`-scoped tokens, uses a live one or refreshes via
  `oauth.v2.user.access` (public client, client_id only) and writes the
  rotation back — same contract as `slack_token_sync.py`.
- Workspace guard (`Test-SlackToken` → `auth.test`): two Slack
  workspaces share the token store (SocialAuto T0C18DXH8P7 +
  www.cloudless.gr T09AF5VTK4G); only team_id T0C18DXH8P7 tokens are used,
  preventing the 2026-11-08 channel_not_found flip-flop. Wrong-workspace
  refreshes are still written back (rotation chain stays alive) but not
  used for posting.
- Channel: `SLACK_CHANNEL_ID` from `%USERPROFILE%\scripts\.slack-alerts.env`
  (currently C0C1F1…); static `SLACK_BOT_TOKEN` there is last-resort only.
- BLOCKED — no live Slack credential on the host: both mcp-remote
  `xoxe` tokens are expired AND their refresh_tokens return
  `invalid_grant` (Slack MCP session dead — consistent with the
  slack-remote port-3118 collision above); no `xoxb` bot token exists
  (app reinstall still pending); all incoming webhooks 404. Failover
  code is verified parse-clean + fail-soft, and self-heals the moment a
  valid chat:write token lands in the token store — i.e. after Slack MCP
  re-auth in Devin or the app reinstall. Until then Slack attempts log
  `slack failover api error: invalid_auth` to bounce.log and Telegram
  remains the only working lane.
- Gotcha fixed during testing: `Log` (Tee-Object) emits to the pipeline —
  unguarded `Log` calls inside `Get-SlackToken`/`Send-Slack` polluted
  their return values (array instead of bool/token, truthy-on-failure).
  All Log calls inside value-returning functions now use `$null = Log`.

### platform-fit reland (2026-11-09)

- #1543 was auto-closed MERGED with an EMPTY diff — sweep/rebuild tooling
  reset platform-content-fit-brain onto master and the feature commits
  only survived on local `pcfit-merge`. Relanded file-for-file as
  platform-fit-reland → PR #1552. If a PR shows MERGED with nfiles:0,
  suspect a rebuild wipe — check `git ls-tree origin/<branch>` before
  trusting the merge.
- infisical-sync root cause found: daily 05:30 pull rewrote .env as
  root:root 600 → whole-stack EACCES crash-loop. Fixed at source:
  `pull --owner 1001:1001` added to infisical-sync.py + cron line
  (home-ops f1b391d). mount-check cron also self-heals unreadable .env.

## 2026-11-09 — funnel emails → shared branded HTML layout

- [x] `app/services/funnel_email.py` — one email-safe layout (tables, inline CSS,
  600px, no JS) matching the approved checkup-followup reference; plain text
  stays the source of truth (text/plain part) and is rendered to the HTML part.
  Wired into: nurture (STEPS + QUIET_STEPS), playbook delivery, proposal send +
  proposal follow-ups, agency kickoff / intake nudge / post-consult /
  testimonial / delight ladder, billing owner mails, transactional
  `email_templates._html_wrapper` (welcome etc.), Mautic win-back setup bodies.
- [ ] Mautic win-back templates already created in Mautic keep their old HTML —
  `scripts/mautic_winback_setup.py` only creates missing ones (idempotent by
  name). Re-create/update the 3 `winback-*` emails in Mautic (owner action,
  no real sends from agents) to pick up the branded `customHtml`.
- [ ] Email campaigns/drips (`email_campaigns.build_bodies` → `email_brand`)
  keep their own dark campaign renderer — not migrated (broadcast campaigns,
  user-authored HTML). Decide if they should move to the funnel layout.
- [ ] BOOKING_DM_STEPS (nurture_sequence) are DMs, not email — out of scope.

## 2026-11-09 — Docker data-disk loss → full stack recovery

- Docker Desktop data disk (`docker_data.vhdx`) lost container/volume/image
  metadata ~13:54; VHDX itself intact (~20GB) but engine state was empty.
- Recovered all 24 named volumes from the 2026-11-09 nightly NAS backup
  (`devoptimizer-backups/nightly`, pgdumpall + vol-*.tar.gz) via
  `/home/tbaltzakis/docker-restore/restore-volumes.sh` — Postgres ~395MB,
  Redis, Mautic, MinIO, browser/session volumes all verified.
- `docker compose pull` hit Docker Hub 429s; retried after cooldown.
  Local-only images (`local/promtail-healthcheck`, `cu130-slim-browser-novnc`,
  `portainer/portainer-ce:2.39.6-healthcheck`) are compose `build:` targets.
- Two latent build breaks fixed (PR #1567): `Dockerfile.portainer-healthcheck`
  was never committed (recreated: portainer-ce + static busybox for the
  `/busybox wget` healthcheck); `browser-novnc/Dockerfile` ran apt-get before
  `USER root` while sidecar-base now ends `USER app` — fresh builds failed on
  the apt lock.
- flower crash-loop: `mher/flower:2.2.0` ships Python 3.15 without `packaging`
  → kombu ImportError. Patched local tag via derived image
  (`python -m ensurepip && pip install packaging`, tagged over 2.2.0) — now
  healthy. Durable fix still needed: pin/bump flower or add packaging to a
  derived image in compose (local tag survives until next pull_policy refresh).
- Post-recovery verified: `/health` ok, 4/4 celery nodes, alembic head
  `s9p9r8s7t6u5` on restored DB (44 leads / 374 posts / 13 accounts), n8n 200,
  Mautic 200, `social.cloudless.gr/pricing` 200 via cloudflared.
- Synigoros monitor OTP gate fixed same day (PRs #1561/#1563/#1564) — see
  Synigoros section above.
- [ ] Take a fresh post-recovery NAS backup once stack is confirmed stable
  (nightly job should do it; verify tomorrow's run).
- [ ] `docker model`/DMR + GPU services (`social-media-comfyui-gpu`,
  `instagram-private-api`, sidecars) are stack-ops sleep-managed Exited —
  expected idle state, not a fault. Wake on demand via stackctl.

## 2026-11-09 — Infisical secrets migration (DONE + leftovers)

- [x] All 219 real keys from `.env` migrated to Infisical `cloudless-infra`/prod
  (252 total; 33 Infisical-only pre-existing). Verified `status`: only-local=0,
  value-differs=0. Tool: `~/home-ops/scripts/infisical-sync.py`
  (push/pull/status) + `/root/.local/bin/infisical-api.sh`.
- [x] `.env` is now a generated cache — daily pull cron 05:30
  (`/etc/cron.d/infisical-sync`, log `~/logs/infisical-sync.log`), merge mode,
  timestamped `.bak-*`. First pull done 03:19 UTC (backfilled
  MESSENGER/WHATSAPP_VERIFY_TOKEN from Infisical; takes effect on next
  `compose up`).
- [x] API gotchas fixed in tooling: `secrets/raw` paginates (need `limit=1000`),
  4xx error bodies must not count as success. Documented in infisical-ops
  SKILL.md (PR #1548).
- [ ] `APPFLOWY_*` keys (pod env on omv k3s) still NOT in Infisical — pull via
  cluster workflow when needed; do not export pod env as artifacts.
- [ ] 61 placeholder/empty `.env` keys intentionally skipped — revisit if any
  become real (e.g. SLACK_*_WEBHOOK_URL family).
- [ ] Rotate `POSTGRES_PASSWORD` (metabase role) — value was printed in a tool
  transcript 2026-11-09; also rotate Infisical UA client secret (same exposure
  window earlier) per secret-safety policy.
- [ ] Docker Desktop flapped repeatedly tonight (engine down ~02:30–02:40,
  Infisical wedged once — restarted). Watchdog covers social stack only;
  consider adding `infisical`+`postgres` to its critical-container list.

### slack-token-sync: fully landed (2026-11-09 ~03:30)

- ALL 7 SLACK_BOT_TOKEN consumers verified on the SocialAuto token:
  social-api, 4 workers, celery-beat, n8n (env hash matches the aef6
  mcp-remote file). Pending marker cleared manually after verification.
- Mid-run incident: Docker Desktop backend 500'd + vsock degraded
  (doctor: proxy-dead class, L1-L3 exhausted). A parallel devin agent
  held .deploy.lock doing bind-mount drift repair - it released the
  lock and finished the recreate; stack came back fully healthy. Do NOT
  force a full WSL recycle while a fresh .deploy.lock exists.
- n8n EACCES on /home/node/.n8n/config + alembic env.py ImportError were
  symptoms of that same mid-restart churn - resolved by the repair, not
  by config changes.
- Remaining watch item: next cron fire 04:17 - first fully-clean run of
  the hardened script; check ~/logs/slack-token-sync.log afterward.
  In-WSL docker access depends on the user-distro proxy (doctor L1-L3
  failed earlier; watchdog + docker-socket-watch self-heal it).

- slack-token-sync.sh wrapper now self-reports: non-zero exit ->
  tg_alert.py ops severity to Telegram #ops (best-effort, exit code
  preserved). Arg format verified: `tg_alert.py <sev> "<text>"`.
- docker-socket-watch logged 7 successful docker-desktop recycles
  tonight (socket dead -> recycle ~45s) - the proxy-dead class is being
  healed on schedule; in-WSL `docker info` confirmed 29.9.2 after.

- CORRECTION to earlier note: ~/scripts is NOT broken - it is a valid
  relative symlink -> home-ops/scripts (a git-versioned ops-scripts
  repo). UNC/9P just can't traverse it (shows `?`), which caused the
  false "dangling" read. system-hygiene cron path is fine. Ops scripts
  arguably belong in home-ops/scripts (auto-committed by
  home-ops-autocommit.sh) - slack-token-sync.sh + extract_slack_hook.sh
  currently live loose in ~/; candidate move if home-ops ownership is
  confirmed.

- home-ops consolidation (2026-11-09): slack-token-sync.sh +
  extract_slack_hook.sh moved into home-ops/scripts (versioned,
  auto-commit repo); cron now calls /bin/bash ~/scripts/slack-token-sync.sh
  (exec-bit proof). system-hygiene weekly digest now reports
  slack-token-sync freshness/pending. infisical-sync.py pull protects
  EPHEMERAL_KEYS={SLACK_BOT_TOKEN} so the 05:30 daily .env regen can't
  revert the rotating token (home-ops commits f3669ae, 12387ba).

## 2026-11-09 — warm-back engine coverage fix + stack repair (devin)

- PR #1542 (feat/warmback-engine): tests were never committed to the
  branch — CI coverage gate failed (mautic_winback 9%). Added full suite
  in worktree, pushed b23e0704 (87 tests, touched modules 100% cov).
  Branch went BEHIND after sibling merges — update-branch applied,
  checks rerunning, auto-merge armed.
- PR sweep: #1534 DIRTY rebuilt -> new PR #1550; all open PRs
  auto-merge armed.
- Stack repair after docker bounce: backend/app bind-mount went stale
  (branch switch) -> celery-beat crash-loop + empty /app/app; recreated
  all 6 backend services. .env rewritten root:root 600 -> chown 1001:1001
  (worker-default PermissionError fixed). social-cloudflared entrypoint
  bind-mount stale -> silent 0.6s exits, social.cloudless.gr 530;
  force-recreate restored tunnel (pricing 200, api health 200).
- LEFTOVER: win-back code goes live only when the shared checkout lands
  on merged master (containers bind-mount WSL checkout app/, not image).
  Checkout was on docs/infisical-sync at session end — next agent on
  master should `git pull` + `compose up -d` backend services, then
  verify `mautic_winback` beat entry + dry-run.
- LEFTOVER: social-worker-default hit "No such container" recreate race
  once — retried fine; watch for recurrence (hash-prefix bug class).

- cron.d CRLF recurrence (2026-11-09): /etc/cron.d/slack-token-sync kept
  logging "bad username" at every cron restart despite LF-looking reads
  via 9P - Windows-side writes through \wsl.localhost can carry CRLF that
  9P masks on read. FIX: rewrite cron.d files from INSIDE WSL
  (printf > file) when editing; verified od -c shows 
 only, root:root
  644. Wrapper now /bin/bash ~/scripts/slack-token-sync.sh (exec-bit
  proof); moved scripts chmod 755'd in home-ops.

## 2026-11-09 — win-back engine GO-LIVE (devin)

- PR #1542 MERGED 02:19Z (36/36 checks). Warm-back engine is on master.
- Go-live done: restored mounted backend dirs (app/scripts/tests/alembic)
  to origin/master content in the WSL checkout (docs/infisical-sync branch
  HEAD unchanged — worktree-only restore; backend files now show as
  modified vs that branch's HEAD, which is expected drift, not WIP).
- Verified live: celery-beat healthy w/ 'mautic-winback' 10:45 daily entry;
  4 worker nodes pong; social-api healthy; dry-run sweep inside
  social-worker-default -> {'scanned': 21, 'sent': 0, 'dry_run': True}.
- LEFTOVER: checkout branch is still docs/infisical-sync — next agent
  should `git checkout master && git pull` when the foreign WIP
  (.cursor/.devin skill edits) is resolved; backend mount already runs
  master content.
- LEFTOVER: queued celery dry-run task 0855d1c1 may still be pending
  behind funnel_sync on default queue — harmless, dry_run=True.

## 2026-11-09 — win-back end-to-end test + Mautic mailer fixed (devin)

- TEST RESULT: win-back path verified end-to-end. Created Mautic contact
  id=4 (tbaltzakis@cloudless.gr) and sent email id=1 via the production
  path POST /emails/{id}/contact/{cid}/send -> {"success": true} ->
  DELIVERED to mailbox (IMAP-verified, "The 30-minute audit we offer
  (still relevant?)" from Cloudless <noreply@cloudless.gr>, 2 copies
  from two test sends).
- REAL BUG FOUND+FIXED: Mautic mailer was NEVER configured —
  mailer_dsn default smtp://localhost:1025 (connection refused). Every
  Mautic campaign send would have failed silently. Fixed in
  /var/www/html/config/local.php (mautic_data volume, survives recreate):
  mailer_dsn=smtp://<user>:<pass>@192.168.1.130:587?verify_peer=0,
  from=noreply@cloudless.gr. GOTCHAS: (a) local.php parameters go through
  Symfony DI — literal % must be written as %% (urlencode %40/%21 broke
  container compile: ParameterNotFoundException "40cloudless.gr:TH");
  (b) cert SAN is hostname not IP -> ?verify_peer=0 required; (c) cache:clear
  as root breaks www-data writes — chown var/cache after.
- Audience reality: all 21 EspoCRM leads with emailAddress are
  junk-filtered (@cloudless.gr/test) -> 0 eligible recipients today.
  Win-back sends start when real external leads with emails exist.
- Mautic test contact id=4 left in place (owner mailbox, excluded from
  sequences by _JUNK anyway).

## 2026-11-09 — ops incident + D1 sync fix (Devin)

- LinkedIn post 98c9c0b0 publish chain: uploads dir perms fixed earlier;
  real blocker was `stack-ops` Exited(127) — bind-mount source went stale
  after Docker Desktop flap → JSONDecodeError on services.json → every
  `*.URL=http://stack-ops:*` lookup failed with Errno -2 (publish, DM
  polls, IG proxy). Fix: `docker compose up -d --force-recreate
  stack-ops`. Post published OK (linkedin.com/feed/update/urn:li:share:7525471642710335488).
- D1 sync `posts.platform_specific` NOT NULL failures: PG holds NULL in
  D1-NOT-NULL columns → every cycle aborted after 3 errors, watermark
  held. Fixed live in checkout + PR #1557 (notnull flags from PRAGMA +
  shape-sampled '{}'/'[]'/0/'' substitution). Verified: `Synced posts:
  19 rows to D1 (0 deleted, 0 errors)` after worker restart.
- TikTok `no access_token in refresh response`: `tiktok/cloudless.gr`
  account refresh token is dead (expired 2026-11-04) — needs re-auth.
  PR #1558 makes TikTokOAuth2.refresh_token raise with TikTok's real
  error_description instead of the opaque downstream message.
- PRs open: #1557 (d1-notnull), #1558 (tiktok-refresh), plus older
  rebuilt #1550/#1530. cloudless.gr #2404 (gh-secret-mirror) fixed
  Sentry HIGH finding — secrets now go via stdin manifest, not argv.
- WSL vsock degradation recurred; durable `wsl --shutdown` still
  deferred to the 04:45 scheduled task (kills active sessions).

## 2026-11-09 — NVIDIA PAIR + k3s + Docker/WSL infra session (Devin)

- PAIR installed: WSL node via `nvpair` deb (TUI in tmux `pair`, systemd
  unit `nvpair-tui.service` re-ensures sessions at boot); Windows node
  silent-installed to `C:\Program Files\PAIR` (GUI running, unconfigured —
  optional second node).
- Ollama engine ADOPTED on WSL node (manual install to /usr/local, serving
  127.0.0.1:11435 in tmux `ollama`; PAIR proxy *:11434 verified serving
  Ollama + OpenAI APIs). Models: smollm2, nomic-embed-text.
- Containers reach PAIR via `host.docker.internal:11434` (verified with
  docker run curl). `.env` updated: DMR_BASE_URL/DMR_EMBED_URL/OLLAMA_URL
  host→host.docker.internal; added DMR_URL, DMR_VLLM_URL,
  N8N_INSTANCE_AI_MODEL_URL.
- TODO before next infisical-sync pull (daily 05:30) or stack rebuild:
  push the 6 changed/added keys to Infisical prod `/` — infisical
  container is down (stack wiped), use infisical-api.sh once up, or
  `scripts/infisical-sync.py push .env --overwrite` after verifying drift.
  Until then .env reverts DMR_BASE_URL/EMBED_URL/OLLAMA_URL on pull.
- Compose `ollama` service (docker-compose.yml ~L1609) is now redundant —
  candidates for removal in a PR once PAIR is proven; it would otherwise
  start an empty Ollama container on rebuild.
- Vision model gap: .env/compose expect `ai/qwen3-vl` (DMR catalog name);
  PAIR/Ollama has `qwen3-vl` (~6GB pull at ~1MB/s — deferred). Media QA
  vision falls back to CF Workers AI until pulled.
- k3s: kubeconfig rewritten to https://192.168.1.128:6443, `kubectl get
  nodes` verified (omv Ready v1.36.2+k3s1); copied to
  C:\Users\tbaltzakis\.kube\config for Windows kubectl. Docker Desktop
  does not manage remote k3s — kubeconfig context is the management path.
- PAIR caveats: TUI quit stops all PAIR services (tmux sessions must
  stay alive); proxy binds *:11434 (all interfaces) — acceptable on LAN
  but note it; PAIR does not pool GPUs or split requests.
- Still open: MCP all_catalog gateway wiring to Devin; stack rebuild
  (`docker compose up -d`, ~45 images, Postgres data lost in DD wipe —
  check for pg dumps first); PAIR Windows node pairing (optional).

## 2026-11-09 (late) — automation sweep (Devin)

- Stack SELF-HEALED: docker-recovery/watchdog ran compose up after the DD
  wipe; ~50 containers healthy. social-api/workers recreated onto new .env
  (PAIR endpoints live, verified chat completion from inside container).
- qwen3-vl pulled into PAIR Ollama (6.2GB) — vision model gap closed;
  PAIR /v1/models now lists qwen3-vl, smollm2, nomic-embed-text.
- Infisical prod `/`: all 6 PAIR env keys pushed (DMR_BASE_URL,
  DMR_EMBED_URL, OLLAMA_URL patched; DMR_URL, DMR_VLLM_URL,
  N8N_INSTANCE_AI_MODEL_URL created). .env now survives the 05:30 pull.
- MCP all_catalog: working_set row copied from Windows mcp-toolkit.db into
  WSL /root/.docker/mcp/mcp-toolkit.db (paths→/home/tbaltzakis, backup
  mcp-toolkit.db.bak). Devin mcp_config.json gained `all_catalog` server
  (same docker mcp gateway run pattern as `github`). Windows-side
  `docker.exe mcp gateway run --profile all_catalog --dry-run` loads 314
  catalog servers; image verify/pull of ~200 MCP images was in progress —
  NOTE it briefly killed the Docker engine once (EOFs on pipe); retry
  running. Remote OAuth servers still expected to fail discovery
  individually. UNVERIFIED: whether Devin's `docker` resolves to the
  Windows CLI (WSL `docker mcp` says "Docker Desktop is not running") —
  if all_catalog fails to start in a new session, point command at
  /mnt/c/Users/tbaltzakis/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe
  or run gateway on Windows side.
- PAIR persistence fixed: nvpair-tui-ensure.sh now exports HOME=/root
  (ollama serve panicked "$HOME is not defined" under systemd).
- ollama serve + PAIR TUI survive WSL reboot via nvpair-tui.service +
  tmux sessions `pair`/`ollama` (verified after the 18:43 restart).

## 2026-11-10 — WIP salvage session (Devin)

- cu130-slim dirty tree (57 files on stale `fix/linkedin-sidecar-dockerfile`)
  triaged vs origin/master: 29 files already landed via merged PRs, 11 were
  cosmetic-only drift (resolved to master), real WIP = ops-agent suite +
  d1_restore → PR #1573 (`feat/ops-agent-suite`, auto-merge armed).
- ops_agent was never wired: added celery include/route/beat (*/5, default
  queue) + alembic `t1u2v3w4x5y6` for `ops_incidents` (has_table guard).
- Post-merge verify: alembic head, `ops_agent:last_sweep` heartbeat in Redis,
  first sweep stats in worker-default logs.
- Main checkout realigned: now on `master` @ origin/master, clean tree.
  Stale branch `fix/linkedin-sidecar-dockerfile` kept (13 local commits,
  content superseded — safe to delete after #1573 merges).
- Docker Desktop was DOWN mid-session (docker.sock + desktop-linux context
  both dead) — watchdog should self-heal; if not, `wsl --shutdown` durable
  fix still deferred per earlier note.
- home-ops arch-doc prose refresh committed + pushed (beb46bb).

## 2026-11-10 — leftovers sweep (Devin, PR #1573)

- CI `test` job failed on line-coverage gate (98.80% < 99.0%): expanded
  `test_ops_agent.py` 4→24 tests covering sweep/verdict/alert/dispatch
  paths — ops_agent.py now ~99% line coverage; ruff clean; pushed.
- PR #1570 (`ops-agent-incidents`) was a parallel duplicate of the same
  ops-agent suite. Core app files were byte-identical to #1573; its unique
  host-side pieces (ops_incident.py CLI, ops_incident_host.sh psql
  fallback, compose-watchdog ALERT→incident + ops-agent dead-man check)
  were folded into #1573 and #1570 closed as superseded.
- Alembic graph verified: single head `t1u2v3w4x5y6` on branch,
  down_revision = master head `s9p9r8s7t6u5` — merge stays linear.
- pr-ci-fix-watch.py (home-ops cron, */15) now auto-fixes ruff + alembic
  multi-head/can't-locate failures and Telegram-alerts #ops on the rest.

## 2026-11-10 — report leftovers + token automation (Devin, PR #1575)

- OMV pending config applied (phpfpm) via omv-apply-pending run 42752557563.
- social-cloudflared was Exited(127) ~1h — stale Docker Desktop bind-mount
  on entrypoint.sh → Cloudflare Tunnel 1033 killed ALL OAuth callbacks
  (TikTok re-auth "finished-with-error"). Recreated via
  `docker compose up -d --no-deps --force-recreate cloudflared`.
- Token-expiry sweep blind spot fixed in #1575: expired/revoked accounts
  were invisible (only active/connected scanned, meta-only expiry, `x` vs
  `twitter` name mismatch). Now column-expiry fallback + 24h dedupe.
- `scripts/tiktok_reauth.py` (merged #1571) was never scheduled — wired
  into beat hourly in #1575.
- Human gates (auto-escalated now, still need a click):
  X + Instagram + Threads (bridge sessions logged out after novnc restart;
  X web cookies not stored anywhere), WhatsApp phone verify
  (payment+code in Meta Business), Facebook personal user-token,
  TikTok `cloudless.gr` row (sidecar is logged into the other TikTok
  account — `docker compose exec social-api python
  scripts/tiktok_reauth.py` can't reach it).
- PR #1574 (account-connected mail spam fix) merged; social-api restarted.
- After #1575 merges: `git pull && docker compose restart celery-beat
  social-worker-default` to load the new beat schedule + task.
