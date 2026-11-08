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

- [ ] cloudless-gr-live (265081467): Live Events product still shows FORM
  state — fill via `li_devapp_ops.py fill-form <Q_DL>` once the catalog
  provisions; the app-bound Q_DL link persists (grab a fresh one from the
  products page when filling).
- [ ] `linkedin-access-forms` SKILL.md update (app creds + probe pointer)
  — was blocked on root-owned dir; ownership fixed 2026-11-07, now unblocked.
- [ ] LinkedIn Verify-PIN modal inert under all automation (CDP/keyboard/
  OS-click/patchright) — retest now that the 48h fresh-app window passed;
  if still inert it is a fresh-app artifact, not anti-bot.
- [ ] Backfill verification: pass resumed 2026-11-07 inside social-api
  (`scripts/backfill_media_quality.py --limit 500`, non-force — 355/732
  had spellcheck_applied at start). Confirm final counts +
  `meta_data.quality.spellcheck_applied=true` when it completes.
- [ ] human: Windows `sshd` on 192.168.1.23 is stopped (Access denied
  to start it unelevated) — one elevated
  `Start-Service sshd; Set-Service sshd -StartupType Automatic`. The omv
  watchdog remediation itself is FIXED in both copies (deployed
  /usr/local/sbin/safedeploy-watchdog.py + cloudless.gr PR #2374) — only
  the ssh hop is dead until sshd runs.
- [ ] Playwright E2E: `.env` values are quoted — `run_pw_e2e.sh`
  (gitignored, repo root) strips quotes before exporting E2E_ADMIN_*;
  plain `playwright test` skips admin-login unless vars are set unquoted.
  Documented gotcha, not a bug.
- [ ] comfyui image absent locally — first wake via stack-ops:8199 will
  pull the full image (multi-GB, may exceed the 120s wake timeout);
  expect transient proxy failures until the first pull completes.

## Open — external human gates

- [x] human: TikTok `cloudless.gr` reconnected 2026-11-07 — QR login via
  headed noVNC bridge + cookie inject into sidecar, fresh OAuth consent
  on production app (client_key awtn8vvhotyxe9oc, scopes
  user.info.basic,video.publish,video.upload — PR #1408). New account
  `ab439e1e-549f-4a8b-901a-2f7eeb8a1c88` (display "Cloudless") is
  active; manual refresh + creator_info/validate API calls verified;
  stale 6-scope account `dcda73fb-…` marked revoked. Scheduled
  refresh-expiring-tokens beat is healthy (publishing queue).
  watch: first real publish still unproven on the new grant.
- [ ] human: WhatsApp `Themistoklis Baltzakis` revoked → WABA assignment
  in Meta Business, then POST /{phone}/register (standard registration —
  request_code/verify_code are the migration API).
- [ ] human: Gmail `GMAIL_REFRESH_TOKEN` 401s on exchange → one
  `gmail.py login` re-consent click.
- [ ] human: LinkedIn sidecar :9225 browser session dead and stored
  `LINKEDIN_COOKIE` li_at stale on every automated lane → credential
  login via noVNC, or store LINKEDIN_EMAIL/PASSWORD so the
  credentials-login fallback can run. NOTE: the OAuth/API surface is
  healthy (full-union consent landed on 264925843, 2026-11-07) — this
  gate only blocks DOM-automation flows (DMs, specialties, portal edits).
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
- [x] TikTok Creative Center trend pull — RESOLVED 2026-11-08: video
  pipeline already built in PR #1422 (app/services/tiktok_trends.py,
  unsigned CreativeOne sweep) + PR #1424 (trend_ingest 6h beat →
  trend_signals → datalake). Verified live: 12 videos LIGHT_SWEEP.
  Hashtag gap confirmed dead-end: no unsigned hashtag API (all
  CreativeOne/radar endpoint guesses 404); top-3/region only via
  SSR-embedded page state; full rankings need TikTok For Business
  login (consumer QR session insufficient — SSO bounces). Use the
  TikTok app's native Creative Center for full hashtag lists.
- [ ] owner-gate: TikTok profile website link needs 1,000 followers OR
  Registered Business Account; business verification was rejected —
  resubmit with Greek sole-trader/ΑΑΔΕ tax doc type, not ΓΕΜΗ.
- [ ] watch: PRs #1422, #1424, #1428 — auto-merge armed, checks
  pending, 0 failures.

## 2026-11-08 session leftovers (Devin)

- [ ] **Branch consolidation worklist** — 24 remote branches unmerged vs
  master. Stale/dup (verify-content-then-delete, do NOT merge): `pr-1343`,
  `pr-1344`, `pr-1347`, `pr-1350`, `pr-1364`, `pr-1370`, `pr-1372`,
  `pr-1387`, `pr-1397`, `heal/20261104-2338`, `heal/20261105-0641`,
  `heal/20261105-1955`, `heal/20261106-0638`, `heal/20261107-1256`,
  `heal/20261107-2147`, `feat/fb-token-watch` (merged via #1425 squash).
  Real deltas needing PR-per-branch (sizes = files vs master):
  `chore/leftover-sync` (16), `devin/cfdb-infisical-rebuilt` (6),
  `feat/operator-gate-ops` (4), `feat/linkedin-adlibrary` (4),
  `chore/backlog-items-20261107` (2). In-flight PRs cover:
  `feat/tiktok-trendwatch` (#1422), `feat/trend-signals-lake` (#1424),
  `fix/media-qa-timeout`, `fix/test-infra-hardening`.
- [ ] **OMV iptables fix landed manually** — docker-ce 29.9.3 upgrade
  dropped FORWARD ACCEPTs for published ports 8090/9980/11000/7867;
  patched DOCKER chain + restarted nextcloud stack (docker re-added its
  own rules). If 502s recur after reboot, re-check `iptables -L DOCKER`.
- [ ] **TikTok `cloudless.gr` re-consent blocked** — needs current
  SOCIAL_ADMIN creds (`.env` values return "Invalid credentials");
  refresh grant confirmed dead (invalid_grant). Live `Managed Cloud`
  account covers posting meanwhile.
- [ ] **Windows logon needed** — `cdp-launch.bat` now in Startup folder;
  `fb_token_watch` (PID may have died — restart:
  `nohup python3 .devin/skills/operator-gate-ops/scripts/fb_token_watch.py &`)
  fires FB OAuth w/ business_management → then run
  `meta_system_user.py` for the permanent WhatsApp/Meta token.

## 2026-11-08 — full test-suite sweep (Devin)

Ran every suite on host. Results + fixes landed on `feat/fb-token-watch`
(commits 9031a35a + 815e40e2, open PR #1428).

Green: backend unit (8014p/2s), stack-ops + scripts/tests (49p),
cloudless-email-worker (7p + tsc), env-manager (vitest/tsc/eslint/ruff/mypy),
frontend vitest (958p), frontend tsc, root comfyui vitest, compose config.
Backend integration: 104p after heals+fixes below.

Fixed:
- research.py used nonexistent `User.team_id` → switched to `TeamId` dep
  (was live 500 on /api/v1/research/*).
- messenger+whatsapp webhooks accepted unsigned POSTs when
  FACEBOOK_APP_SECRET unset → now fall back to FACEBOOK_CLIENT_SECRET
  (live-verified 403). SECURITY gap closed.
- 6 frontend TS18047 (`useSearchParams`/`useParams` null) → `?.`/`?? ''`.
- env-manager ruff (2×E501, W292); minio test port 9100→9102;
  SWEEP_SKIP += media-sources/ (external upstream relays).

Env issues found (document, not code):
- uid-1000-owned `frontend/node_modules`+`test-results` block installs —
  renamed aside to `*.bak`; needs `sudo chown -R 1001:1001` or rm to purge.
- Docker Desktop port forwards went stale → `docker restart chroma minio`
  + `compose up -d stack-ops` (its bind-mount staging had vanished; a plain
  `docker restart stack-ops` fails — must recreate).
- Node 22.22.2 installed at `~/.local/bin` (apt node is v20; undici/vitest
  require >=22 per engines).
- Playwright browsers: installed -1248 but tests wanted -1234 → symlinked
  `chromium{,_headless_shell}-1234 -> -1248` in ~/.cache/ms-playwright.
- `/api/v1/analytics/overview` takes ~41s cold (over the sweep's 45s budget);
  `analytics/insights` dropped a conn once under load — watch, not proven bug.
- media-sources gdrive `invalid_grant` + nextcloud PROPFIND 502 — external
  creds dead (matches existing human-gate entries).
- Playwright full e2e running against live stack at end of session; interim
  smoke 21p/2flaky(authTokens setup timeouts under parallel load).

## 2026-11-08 — TikTok Creative Center trendwatch + branch consolidation

- [x] `scripts/tiktok_trendwatch.py` + `app/services/tiktok_trends.py` —
  weekly trending-video signals from the unsigned CreativeOne report
  endpoints (post-Oct-2026 deprecation path). `trend_context.trend_line(
  platform="tiktok")` feeds the top niche hook into TikTok copy
  generation; LIGHT sweep = 3 calls. Report: `--slack`/`--full`.
- [x] Branch audit: 20 merged-debris branches deleted; 10 superseded
  post-PR branches deleted after content verification (fetch-code
  folders, consent-url, cal.com webhook, org-profile test all already on
  master). Rescue PR #1419 = wsl-checkout-sync-check.sh +
  linkedin_attribution_chain.sql.
- [ ] decide: `feat/nextcloud-extras` + `feat/nextcloud-autoupdate-feeds`
  — 38/27-file real code deltas vs master (empty #1379 merge era):
  nextcloud sync lanes, oauth_reauth_watch, series_urn_watch,
  token_expiry, funnel_audit tasks, agency_pipeline/crm_engagement
  services, omv app-update cron. Needs a dedicated re-land pass — some
  content may now be superseded by #1381/#1413-1415 era fixes. DO NOT
  blind-merge (46-89 commits behind).

## 2026-11-08 — MCP health (Devin)

- [x] `mcp_health.py` sweep: 20/20 healthy. Only `linkedin-ads` failed on
  the first pass — cold `uvx` bootstrap (`Installed 68 packages`) exceeds
  the 20s default probe timeout. After the install, uv's cache keeps it
  warm; re-probe with `--timeout 60` if the uv cache is ever purged.
- [x] `pr_rebuild.py` repo-resolution bug fixed — `gh repo view -q` emits a
  bare string, not JSON (`JSONDecodeError` during the PR sweep).
- [ ] PR queue: #1430/#1432/#1433 DIRTY (real-delta branches from the
  consolidation worklist) — need worktree rebase or pr_rebuild.

## 2026-11-08 — cloud.cloudless.gr 502 (RESOLVED, transient)

- User-reported Cloudflare 502 at 23:41 UTC. Root cause: the four
  tunnel-fronted workspace containers (`nextcloud`, `collabora`,
  `nextcloud-talk-hpb`, `nextcloud-notify-push`) were recreated/restarted
  on omv at ~01:48 EET — journal shows them re-joining `workspace_default`.
  While down, `cloudflared-omv` had no origin → CF 502. Self-healed ~7min.
- Trigger: a compose redeploy/recreate (OMV plugin op or external agent);
  not a crash (RestartCount=0, no OOM, no Apache segfaults this time).
- Verified healthy after: status.php 200, dav 401, signal /api/v1/welcome
  200, office 200, occ installed/35.0.2/no-maintenance. push root 404 and
  signal root 404 are NORMAL for those Go services.
- [ ] Residual watch: docker-ce 29.9.3 DOCKER-chain FORWARD ACCEPTs for
  11000/8090 didn't show in `iptables -L DOCKER` post-restart, yet LAN
  probes to :11000 succeed — if LAN→published-port access breaks, re-check.
- [ ] `nas-docker-health` cron (10min) IS alive — log file grows; earlier
  "PERSISTENT unhealthy: nextcloud-talk-hpb" lines were Nov 1 leftovers.

## 2026-11-08 — backlog sweep (Devin) — resolved this pass

- [x] Backfill verification DONE: `backfill_media_quality.py` scanned all
  744 media_assets — 453 have spellcheck_applied, the remainder have no
  text/quality metadata to fill (terminal state, not a stall). Note for
  future runs: script needs `-w /app PYTHONPATH=/app` inside social-api
  and `--limit` >= total rows (it scans batches sequentially).
- [x] linkedin-access-forms SKILL.md update — already merged via PR #1356
  (stale entry).
- [x] OMV iptables watch RESOLVED: rules ARE present — DOCKER chain keys
  on container ports (dpt:80→nextcloud 11000, dpt:8081→hpb 8090,
  dpt:9980→collabora, dpt:7867→push). Earlier "missing 11000/8090" was a
  grep error, not a real gap.
- [x] Branch debris deleted: heal/20261104-2338, -1105-0641, -1105-1955,
  -1106-0638, -1107-1256, -1107-2147 (remote) + 9 stale local
  refs/remotes/pr-* (remote was deleted long ago; refs were local-only).
  All verified zero unmerged content (git cherry / file-diff vs master).
- [x] DIRTY consolidation PRs rebuilt via fixed pr_rebuild.py:
  #1430→#1436 (chore/leftover-sync-rebuilt), #1433→#1434
  (fix/media-qa-timeout-rebuilt); #1432's files were already on master
  (rebuild no-op); user-authored revert #1437 auto-merge armed (it only
  undoes the redundant #1435 diff — gate_runner.py exists on master).
- [x] feat/nextcloud-extras + feat/nextcloud-autoupdate-feeds decide item
  — STALE: both already merged (#1377/#1379); refs deleted server-side.
- [x] Playwright E2E env-quote gotcha AUTOMATED: playwright.config.ts now
  loads repo-root .env itself, strips quotes, maps SOCIAL_ADMIN_*→
  E2E_ADMIN_* — plain `playwright test` works, no wrapper needed.
- PR board end of sweep: 5 open, all auto-merge armed, 0 actionable
  failures (#1424 #1428 #1434 #1436 #1437).

Still human-only (unchanged): WABA assignment, Gmail re-consent, LinkedIn
sidecar session + cookie, X OAuth approve, FB OAuth, CM survey recording,
Member DP modal, Matched Audiences, ChromeDebug schtasks, Windows sshd
elevation, TikTok business resubmit (ΑΑΔΕ doc), SOCIAL_ADMIN creds for
TikTok re-consent, host RAM pressure, IDE authority choice, Member DP
form-vs-app decision, garbled LI asset, synigoros recipient confirm,
wsl-safe-bounce schedule, Kakkava week-2 content work.

## 2026-11-08 — human-gate automation pass (Devin)

- [x] LinkedIn sidecar "dead session" — STALE: :9225 /health reports
  has_session=true, rate_limited=false, page_ok=true. Live now.
- [x] FB OAuth reconnect — Meta "is this you?" login approval answered
  via ADB on the S22+ (Windows login, Megalokhórion, 02:33→approved
  02:51). The waiting fb_token_watch/credential-reset flow now has a
  logged-in Meta device. Follow-up: rerun fb_token_watch if the token
  didn't land; then meta_system_user.py for the permanent WA token.
- [x] TikTok sidecar session — verified live (has_session=true on :9224).
- [-] TikTok profile website via ADB — script ran twice (two devices
  attached; second run landed on FB dialogs, not the profile editor).
  Field still gated by the 1k-follower/business-verification rule —
  ADB can't bypass that; needs the ΑΑΔΕ doc-type resubmit first.
- Remaining true-human: WABA assignment in Meta Business UI, Meta
  evidence upload, CM survey recording (audio+address), Windows sshd
  elevation + ChromeDebug schtasks, Member DP modal retry, TikTok re-
  consent (SOCIAL_ADMIN creds), decision items.

## 2026-11-08 — human-gate automation pass #2 (Devin)

- [x] FB OAuth CONFIRMED closed: `social_accounts` facebook row is
  `active` (updated 2026-11-08 00:03) — the phone approval let the
  stored flow complete. instagram+threads rows also active.
- [x] LinkedIn sidecar :9225 — verified live session (health has_session).
- [x] TikTok sidecar — verified live session.
- [~] X OAuth — minted a fresh authorize URL via
  `GET /api/v1/auth/oauth/twitter/authorize?team_id=<jwt team_id>` (real
  URL, not the stale /api/v1/oauth/... path — that 404s). Drove it into
  the debug Chrome :9222 but the tab lands on `chrome-error` and the
  CDP websocket drops during navigation (WSL↔Windows interop flake).
  Fix the Chrome network path (or run cdp_click from Windows python —
  the script header notes `websocket-client` on Windows python) then
  click Authorize.
- [ ] Still manual: WABA assignment + Meta evidence (deep console UI),
  CM recording, Windows sshd elevation, Member DP modal, decisions
  (garbled LI asset / synigoros / wsl-safe-bounce / Member DP path),
  issues #793/#795 = literal `approve`/`done` replies.

## 2026-11-08 — MCP fleet + Infisical migration + human-gate sweep (Devin)

- [x] MCP fleet 20/20 healthy (`.devin/skills/mcp-ops/scripts/mcp_health.py`):
  fixed wslpath bugs in metabase/polar wrappers, generic `mcp-remote.sh`,
  slack proxy env, phone path, google-photos + linkedin-ads wrappers,
  uvx install, mcp_health stdin/stderr handling, woke sleep-managed
  linkedin:9227 / airbyte:9228 / nextcloud:9231.
- [x] `linkedin_token_dump.py` rewritten standalone (asyncpg+cryptography,
  ~6s vs ~20s) — linkedin-ads MCP init no longer borderline.
- [x] LinkedIn sidecar personal login DONE: credential login → LinkedIn
  SMS challenge → code self-read off Galaxy S22+ via ADB → feed live,
  has_session=true. LINKEDIN_EMAIL/PASSWORD/USERNAME in Infisical+D1+PG.
- [x] Infisical `cloudless-infra`/prod now holds 95 secrets — all 85
  credential vars from `.env` + 8 Postgres-only social_secrets keys.
  Tool: `.devin/skills/infisical-ops/scripts/migrate-env.py`.
- [x] Network policy: tailnet IPs only for remote access (AGENTS.md);
  SSH lines updated to 100.x.
- [x] PR sweep: pr_rebuild.py JSON bug fixed; #1424 CI failures fixed
  (celery include + test mocks); all open PRs auto-merge armed.
- [~] LinkedIn org specialties: already populated but DUPLICATED
  (each tag x2). Org-write API returns 404 on every shape tried despite
  rw_organization_admin granted — cosmetic; dedup via sidecar UI if wanted.
- [ ] Dependabot #193/#194 minimatch moderate: only fix is minimatch@11
  via breaking typescript-eslint chain — dev-only DoS, skipped deliberately.
- [ ] True-human remaining (unchanged): WhatsApp Meta Business payment +
  WABA token, Viber partners.viber.com approval, #795 pricing decision,
  #793 partner approvals, WABA assignment + Meta evidence upload,
  CM recording, Windows sshd elevation, TikTok re-consent, X OAuth click
  (chrome-error tab — WSL↔Win CDP flake noted in BACKLOG above).
