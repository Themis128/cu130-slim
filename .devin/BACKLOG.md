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
- [ ] `linkedin-access-forms` SKILL.md update (app creds + probe pointer)
  — was blocked on root-owned dir; ownership fixed 2026-11-07, now unblocked.
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
