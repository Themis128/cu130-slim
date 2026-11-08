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
  request_code/verify_code are the migration API). `WHATSAPP_PHONE_NUMBER_ID`
  still unset; Meta Business billing/payment is the hard prerequisite.
- [ ] human: Viber bot — partners.viber.com/webadmin.viber.com RETIRED
  (verified 2026-11-08, see viber-ops SKILL.md). No bot exists on the
  phone account; new bots are commercial-only → apply via Rakuten Viber
  / partner (respond.io docs: cecile.tizon@viber.com). Once a token is
  issued: `viber_token_fetch.py --token <T>` → `viber_provision.py`.
- [ ] human: TikTok business verification — needs Greek sole-trader/ΑΑΔΕ
  tax certificate under the correct doc type (ΓΕΜΗ submissions rejected);
  unlocks profile website field + full Creative Center rankings.
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
## 2026-11-08 — FB OAuth healed permanently; WhatsApp WABA gone (needs console)

- [x] FACEBOOK_PASSWORD → Infisical prod + Windows vault `cloudless-gate:facebook.com`.
- [x] FB OAuth re-consent completed via debug Chrome CDP (:9222 WSL→172.22.144.1:9229→:9330 bridge; forwarder script /tmp/cdp_fwd.py — recreate if dead).
- [x] `social_accounts` facebook: user row `9355ed63` (Themistoklis Baltzakis) + page row `ad83c946` (cloudless.gr, id 116436681562585) BOTH `active`, user token `expires_at:0` = never expires, scopes include business_management + whatsapp_business_management/messaging. FB re-auth problem is permanently solved.
- [ ] WhatsApp `77f17091` still `revoked`: WABA `1073707258453499` + phone_number_id `1334613883061552` DELETED on Meta side (Graph: object does not exist). Business `1558125105019725` has zero WABAs. Needs: Business Manager console → create WABA → add +306977777838 → SMS code (user's phone; SMS path was rate-limited) → register. Then update meta_data.phone_number_id + token.
- [ ] System-user token mint BLOCKED: app `1936126137016578` lacks capability — `POST /{bid}/system_users` → (#100) "apps with the capability". Enable Business Management/system-user access in App Dashboard → Advanced Access, then rerun mint (scopes incl. whatsapp_*).
- [ ] TikTok `cloudless.gr` (open_id -0008cAv…) re-consented + live-verified 200; `dcda73fb` old-app row is stale orphan — cleanup candidate alongside tiktok dup rows `ab439e1e`/`7c33ed15`.

## 2026-11-08 — Android phone on ADB (SMS-OTP lane live)

- [x] Phone `RFCTA0AEKZP` (Samsung) connected USB + USB-debugging authorized — visible via Windows `adb.exe devices` (`device` state). WSL `adb` is BLIND to it (only stale `emulator-5554` offline) — always use `/mnt/c/Users/tbaltzakis/AppData/Local/Android/Sdk/platform-tools/adb.exe` or `adb.exe` on Windows.
- [x] SMS-OTP automation lane confirmed: `human-gate-bridges/scripts/wa_verify.py` polls `content://sms/inbox` via those adb paths (+ Termux :8022 + doveadm mail fallback). Unblocks WhatsApp WABA phone registration (+306977777838 SMS code) and any future SMS-2FA gates — no human code-reading needed while phone stays plugged in.

## 2026-11-08 — WhatsApp account pulled OUT of cloudless.gr business (moving to another portfolio)

- [x] WAAC `+30 697 777 7838` / display `Cloudless` (created earlier via BM wizard, status Unverified) — phone number DELETED from it via BM → WhatsApp accounts → row ⋯ → Delete phone number (password gate: FACEBOOK_PASSWORD from Infisical via `docker exec infisical node` — host :8090 port-forward is dead post Docker restart, use container IP/exec).
- [x] Empty WAAC shell then removed via detail-panel ⋯ → "Remove from business portfolio" → Delete WhatsApp account. BM now shows "No WhatsApp accounts added" under business `1558125105019725`.
- [x] Phone side: WhatsApp Business app (`com.whatsapp.w4b`, `RFCTA0AEKZP`) backed out of the coexistence `InputVerificationCodeActivity` screen — app account + chats untouched; number stays registered to the app.
- [ ] USER ACTION: re-add the WhatsApp Business account under the TARGET business portfolio (the one- business-per-WABA rule blocked it while it lived in cloudless.gr). Then: phone verify (SMS reads itself via `wa_verify.py` ADB lane) → Messaging account → new phone_number_id → update `social_accounts` whatsapp row `77f17091` meta_data + token.
- [ ] Still open from before: app `1936126137016578` lacks system-user capability (#100) — needs Advanced Access for whatsapp_business_management mint; TikTok stale rows `dcda73fb`/`ab439e1e`/`7c33ed15` cleanup candidates.
- [ ] NOTE: `/tmp/wa_coex_watch.sh` watcher is now MOOT — it polled the Meta embedded-signup unblock for coexistence under cloudless.gr; the account moved businesses instead. Safe to kill if still running.
- [~] WhatsApp coexistence automation re-armed (watcher v2, PID varies — check `pgrep -f wa_coex_watch2`): `/tmp/wa_coex_watch2.sh` → `/tmp/wa_coex_probe.py` polls dev-console every 30 min/48h, detects the account-level "temporarily blocked" cooldown (toast is transient — probe polls 60s post-Continue), on UNBLOCKED runs `/tmp/wa_coex_drive.py` (tab-targeted, hunts access code → `/tmp/wa_access_code.txt`) + kicks `/tmp/wa_phone_link.py` (ADB actuator: code entry, share-history confirm, camera permission; alerts on biometric/QR gates) + Telegram P2. NOTE: dev-console business selector offers ONLY cloudless.gr — the user's target business is NOT visible to this FB session (me/business_users = cloudless.gr only), so the actual add-under-other-business step happens in the user's own BM session; watcher only signals the cooldown lifting.
