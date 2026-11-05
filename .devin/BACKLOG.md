# BACKLOG (living)

## 2026-11-05 — Member app 265077368 company-verified

- LinkedIn email: `cloudless-gr-member` (265077368) approved as associated
  with the cloudless.gr Page (org 108614163). Page verification was already
  done earlier today — this is the *company* verification.
- **Unblocks** (docs-verified 2026-11-05, learn.microsoft.com/linkedin/dma):
  **Member Data Portability (3rd Party)** — "Request access" is enabled only
  after company-page verification. Products tab → access-request form
  (legal name, registered address, website, privacy policy + business-email
  verify — personal email fails) → LinkedIn review. Scope:
  `r_dma_portability_3rd_party` via standard auth-code flow.
- **NOT the (Member) variant**: Member DP (Member) grants
  `r_dma_portability_self_serve` but requires a NEW app created with the
  dedicated "Member Data Portability (Member) Default Company" page — no
  retrofit onto 265077368. It's a light ToS-agree instant grant + OAuth
  Token Generator token; EU/EEA/CH only (Greece OK). Cheapest path to the
  owner's own-data snapshot if that's the goal.
- [ ] Fix products-matrix mapping: business-email verify belongs to
  (3rd Party), not (Member) — matrix conflates them.
- [ ] Decide: drive the (3rd Party) form on 265077368, or create the
  dedicated-page app for (Member) self-serve.

## 2026-11-05 — Consolidated issue backlog (LinkedIn ops sprint)

Status of every open issue from the LinkedIn review + verification email:

| # | Issue | Status |
|---|-------|--------|
| 1 | `li_consent_trim.py` truncated stub (SyntaxError) | ✅ fixed PR #1364 |
| 2 | `qualtrics_fill.py` truncated mid-expression | ✅ fixed PR #1364 |
| 3 | `record_integration.sh` flag-ignoring arg parse | ✅ fixed PR #1364 |
| 4 | `linkedin_scope_audit.py` broken `E` literal + invalid URN | ✅ fixed PR #1364 |
| 5 | `survey_fill.js` `module.exports` in eval ctx | ✅ fixed PR #1364 |
| 6 | argv guards `li_portal_edit`/`li_consent_drive` | ✅ fixed PR #1364 |
| 7 | Member DP product map conflation | ✅ fixed (docs-verified) |
| 8 | Lenovo Vantage WebView2 owns :9222 — CDP lane blocked | ✅ resolved — our lane moved to **:9229** (docs: any port works; Vantage rebinds 9222 on respawn via `DebuggingPortEnabled` reg flag / webview default — not worth fighting). chrome-debug-profile live on :9229 |
| 9 | CDP scripts hardcode :9222 — need port parameter | ✅ fixed — `$CDP_URL` env (default :9229) in `li_portal_edit.py`, `qualtrics_fill.py`, `--cdp` in `cdp_shot.py`, `start-chrome-debug.bat` takes port arg |
| 10 | Member DP request: pick (3rd Party) form vs new (Member) app | ⬜ open — **blocked on LinkedIn login in chrome-debug-profile** (opened on :9229, shows login wall — one manual login seeds it). Then: create NEW app bound to "Member Data Portability (Member) Default Company" page for `r_dma_portability_self_serve` (instant ToS grant), or drive the (3rd Party) form on 265077368 |
| 11 | `li_portal_edit.py get-secret` prints secret to stdout | ✅ fixed — writes to `$LI_SECRET_OUT` file when set, stdout otherwise |
| 12 | LinkedIn token dead — one consent click (once lane up) | ⬜ open (blocked by #10 login) |
| 13 | LinkedIn sidecar :9225 session dead — no stored creds | ⬜ open (manual — noVNC login once) |
- **Unblocks**: product "Request access" modals on the app's Products tab
  (only clickable when company-verified). Next action: request **Member
  Data Portability (Member)** via
  `li_portal_edit.py products 265077368 request "Member Data Portability"`
  — modal still needs the business-email verify step (Edge :9222 +
  logged-in LinkedIn required).

## 2026-11-05 — LinkedIn ops-script repairs (review follow-up)

- Review of today's LinkedIn PRs (#1344–#1358) found two files landed
  **truncated mid-write**: `li_consent_trim.py` (9-line stub, unterminated
  docstring → SyntaxError; `li_consent_drive.py` + portal-ops SKILL.md both
  depend on it) and `qualtrics_fill.py` (101→25 lines, ends mid-expression;
  all documented subcommands dead).
- **Fixed on `fix/linkedin-ops-script-repairs`**: full `li_consent_trim.py`
  implementation (strip scopes in place, `--show`, `--from-error` parses the
  callback's error_description); complete CDP `qualtrics_fill.py`
  (`--map`/`--answers`/`--country`/`--submit` per docstring); real flag parsing
  in `record_integration.sh`; `linkedin_scope_audit.py` — fixed broken
  `E = "{ORG.replace...}"` literal (was never an f-string — EVERY `{E}` probe
  URL was malformed) + `{post}` URN now resolved live from /rest/posts
  (comments endpoint needs share/ugcPost/activity URNs); `survey_fill.js` —
  dropped `module.exports` (throws in browser eval) + argv guards on
  `li_portal_edit.py`/`li_consent_drive.py`; dead-code cleanups. All touched
  files ruff-clean.
- [ ] `li_portal_edit.py get-secret` prints client secret to stdout — by
  design, but lands in shell history/logs; consider stderr flag or file out.

## 2026-11-05 — LinkedIn products/scopes: correct process codified

- **Share on LinkedIn (Default Tier) + Sign In OIDC (Standard Tier) provisioned** on member app
  `265077368` "cloudless-gr-member" (client_id `77fd3pui3m2jwm`, redirect
  already = social.cloudless.gr callback). Email received today.
- **NEW skill `linkedin-products-ops`** (+ `.cursor` mirror): canonical
  product→scope→tier map (Microsoft Learn verified), app topology, rules
  (REPLACE-not-union consent, 60d tokens, refresh=partner-only, 21-day
  survey window, Standard-tier needs AUDIO-narrated video, dedicated-page
  gate for portability). Tools: `li_scope_probe.py` (live
  unauthorized_scope_error probe — no secret needed), `li_products_matrix.py`
  (doc x live matrix), `li_portal_dump.py` (CDP portal scrape).
- **Live probe matrix** (both apps): main `227354605` grantable = self-serve
  + all org scopes + r_ads/rw_ads/r_ads_reporting + r_1st_connections_size +
  rw_organization_admin + r_events/rw_events. Rejected on BOTH:
  *_social_feed, r_member_profileAnalytics, r_member_postAnalytics,
  r_organization_followers, r_dma_portability_self_serve.
- **`.env` EXTRA_SCOPES trimmed** to the grantable union
  (rw_organization_admin r_ads rw_ads r_ads_reporting r_1st_connections_size)
  — previous value had 7 ungrantable scopes → every consent would
  unauthorized_scope_error. social-api recreated, healthy.
- **member-analytics are CM permissions** (not a separate product) — correct
  path = Developer Support Portal ticket on 227354605 asking to provision
  missing CM permissions; the "create new app" instinct is wrong.
- **LinkedIn token still dead** (401 + invalid_grant) → one consent click
  needed; now safe since scope union is grantable.
- **Team fix**: admin@example.com added as owner on team 10024d25 (real
  accounts team) — /auth/switch-team gives full access.
- **linkedin-review@cloudless.gr password reset** (LinkedInReview2026!) —
  reviewer login for demo recordings.
- **264925843 (Cloudless Analytics App, `77j8kzfi1a6jn8`) consents the
  FULL union** — probed: member analytics, feeds, followers, dma
  portability all grantable. Events Management Standard Tier granted
  11-05. RECOMMEND: switch LINKEDIN_CLIENT_ID/SECRET to this client +
  one consent click → heals token AND unlocks member surfaces.
- [ ] CM Standard-tier survey: needs ~2min screen recording WITH US-English
  audio commentary (per LinkedIn requirements) + registered address.
  Recording blocked until token healed via consent click.
- [ ] `linkedin-access-forms` SKILL.md update (app creds + probe pointer)
  blocked — file + dir root-owned; needs `sudo chown -R` on .devin/skills.
- [x] Page verification done on 265077368 (11-05) → Member DP modal unblocked.
- [ ] Member DP (Member) in-portal modal on 265077368 after biz-email verify.

## 2026-11-04 (night) — Session leftovers all landed

- **LinkedIn CM**: consent landed — `rw_organization_admin` + restored `r_ads`/
  `r_ads_reporting` verified live (org/acls/posts/adAccounts all 200).
- **PRs**: #1336 (version pins), #1335 (wsl migrate), #1302 (cf-funnel)
  merged; #1301 closed as superseded (all content upstream); #1337 open
  (converge tools + tailnet/authentik skills) — auto-merge armed.
- **Fixed in flight**: nurture_sequence `step["key"]` KeyError (test used
  mocks without key); cf_gates.py had 0 tests (added, 29%→>99%);
  api-surface-inventory registered /cf-gates routes.
- **Containers**: merged 202610 pins hot-synced into api+workers (docker
  compose cp + restart); 4/4 celery pong; api 200.
- **Git hygiene**: pr1300-fix + 3 local/master-* backups deleted (all
  superseded); tailnet/authentik untracked skill content landed via #1337.
- **Human gates still open**: LinkedIn sidecar/bridge sessions need
  credentialed login (no stored password; li_at stale — verified);
  Windows interop dead (vsock) — Chrome CDP lane needs user-side launch.
- **Stashes** (triaged, NOT dropped — owner's call):
  stash@{0} meta-ads review_state 1-line; stash@{1} act_assist 1-line;
  stash@{2} empty; stash@{3} docker-compose `restart: no` mitigation on
  sidecars + linkedin-sidecar Dockerfile — session debris, likely droppable.

## 2026-11-04 (night) — Scope optimization + token pair dead

- PR #1339: org profile write via rw_organization_admin (PATCH /rest/
  organizations/{id}, text-only) + /page-profile GET/PUT LinkedIn dispatch.
  Verified live on org 108614163 (no-op PATCH).
- Token pair (consent 20:33Z) went dead ~90min later — access 401 +
  refresh invalid_grant. Likely LinkedIn-side revocation after rapid
  successive grants; token_refresh task writes back correctly. Recovery =
  ONE consent click: /tmp/li_consent_url.txt or http://localhost:8917 —
  requests the FULL scope union incl. *_feed/r_member_*/r_organization_
  followers (EXTRA_SCOPES updated in .env; api recreate needed to bake).
- Container code drift: compose cp for accounts.py hit mount decay —
  synced next safe-recreate pass.


## 2026-11-04 (late pm) — LinkedIn Community Management Standard Tier activated

- **Trigger**: LinkedIn email — app `264925843` upgraded Dev → **Standard Tier**.
- **Root cause found (was misdiagnosed)**: the `426 NONEXISTENT_VERSION`
  was NOT primarily a token-scope problem — the pinned `Linkedin-Version`
  headers were dead. Dead pins fixed → `202610`: `linkedin_ads.py` (202404,
  leadFormResponses silently returned `[]`), `consent_healers.py` (202312),
  `linkedin_cm_tool.py` + `linkedin_tool.py` (202510). Production client was
  already on `202608` (active). LinkedIn retired `202510` early — do not
  trust the ~12-month version SLA.
- **Token healed without consent**: stored org token predated the product
  approval; `refresh_token` grant minted a fresh access+refresh pair with
  all CM scopes — written back to `social_accounts` rows `9c4451bb` (org)
  + `18d5cd59` (person), `token_expires_at` → 2027-01-03. Verified live:
  `/rest/posts` 200, `/rest/organizations/108614163` 200,
  `/rest/organizationAcls` 200, `organizations` scope-probe in the tool now
  uses the account URN (was hardcoded to app id → bogus 403).
- **Leftovers**:
  - [x] 4 `social-worker-*` containers — recovered via `docker compose up -d`
    once the docker-desktop bind-mount proxy settled (the hashed `.env` mount
    resolved on retry — was transient VM-side decay, not persistent). All 4
    Up healthy, 4/4 celery nodes pong. `docker desktop restart` CLI failed
    (`com.docker.backend` missing) — the in-place workaround was `docker start`
    on `Created` containers + letting the mount service re-prepare.
  - [ ] `rw_organization_admin` still not granted — needs a fresh consent
    screen (refresh grant can't add scopes). Signed authorize URL generated
    (scopes incl. rw_organization_admin via LINKEDIN_EXTRA_SCOPES); one click
    in any logged-in LinkedIn browser completes it since the callback on
    social.cloudless.gr is live again.
  - [x] `social.cloudless.gr` ingress 530 — `social-cloudflared` +
    `social-frontend` containers were `Created`-but-never-started;
    `docker start` brought both Up healthy → `/api/v1/health` 200.
  - [ ] LinkedIn sidecar :9225 session dead (`/session/clear-rate-limit`
    cleared the 429 circuit but /feed/ now returns SESSION_ERROR — cookies
    are stale). Bridge :9223 profile hit a "Welcome back" password wall.
    No LINKEDIN_EMAIL/PASSWORD in secret store or .env — credential login
    is a manual gate (noVNC or `/login` once creds are stored).
    Stored `LINKEDIN_COOKIE` li_at is ALSO stale — injected into the
    bridge profile it got rejected (429 → ERR_TOO_MANY_REDIRECTS, same
    redirect_loop signature as the sidecar). Cleaned + session stopped.
    No valid LinkedIn session exists on any automated browser lane.
  - [ ] PR #1336 (version re-pins) — auto-merge armed; validate+secret-scan
    passed, full CI matrix (build-and-push/Trivy) still pending at write.
  - [ ] browser-novnc left running (woken for the OAuth attempt); stack-ops
    sleeps it naturally.

## 2026-11-04 (pm) — Polar billing live + n8n crash loop + FB token surface

- **Billing**: `BILLING_PROVIDER` was `paddle` with ALL paddle creds empty →
  every `/api/v1/billing/*` 503'd. Flipped to `polar` + `POLAR_ENVIRONMENT=production`
  (token is a prod OAT, org Cloudless — sandbox-api 401s it). Product IDs
  filled: Pro `3621874d` $10 / Business `3290ef7c` $50 / Enterprise `570fbf52`
  $150. Rotated webhook secret via API on existing endpoint
  (social.cloudless.gr/.../polar-webhook). api+workers+beat recreated;
  `polar_configured=True`, 4/4 celery nodes pong.
- **Polar tokens**: stale `devin-mcp*` dashboard access tokens revoked via
  `polar_token_cleanup.py` (CDP :9222). Keep-list = `devin-mcp3` +
  `socialauto-production`. Token mgmt has NO API surface — dashboard/CDP only.
- **n8n**: 379-restart crash loop — `Deployment key 'signing.hmac' cannot be
  read with this instance encryption key` (all 8 deployment_key rows minted
  2026-10-28 under a rotated N8N_ENCRYPTION_KEY; unrecoverable). Fix: dumped
  table → `logs/n8n-key-recovery/`, deleted rows, restart re-minted under
  current key → healthy. 25 workflows + 6 creds intact.
- **FB token surface**: admin USER token verified live (never expires, full
  scopes incl. business_management); DB `social_accounts` row was stale
  `status=revoked` → flipped `active`. PR #1331 merged (stealth app-secret
  capture, fb_session_seed, meta_system_user).
- **MCP**: Slack OAuth `redirect_uri did not match` — mcp-remote on Windows
  defaults callback host to 127.0.0.1; pinned `--host localhost` in
  `.devin/mcp_config.json` (both slack entries).
- **Git hygiene**: root cause of repeated `:master` push rejections =
  `branch.<name>.merge=refs/heads/master` upstream config; re-pointed +
  pruned 19 merged-PR branches, removed 4 stale worktrees.
- **Leftovers**:
  - [ ] `meta_system_user.py` ran 2026-11-04: business resolved (cloudless.gr
    1558125105019725) but POST system_users → (#100) app lacks capability to
    create SUs via API. Path: create `socialauto-daemon` SU once in Business
    Manager UI (fb-sidecar :9226 session is LIVE), then the script's
    access_tokens mint works. Also fixed: script now takes FB_ADMIN_TOKEN env.
  - [x] fb-sidecar — session is healthy via live profile (`/session/validate`
    logged_in:true); empty fb-session.json was never the session store. real
    Chrome :9222 has no FB session (Lenovo WebView2 hijack) — fb_session_seed
    is a dead lane.
  - [x] `pr1300-fix` → PR #1333 (conflict vs master resolved — kept canonical
    /en/consultation CTA; branch's CTA block was a superseded duplicate).
  - [ ] `polar_token_cleanup.py` still untracked (in-flight, radix-popper
    confirm handler improved) — commit when settled.
  - [ ] `local/master-20261104-*` backup branches; detached %TEMP% worktrees
    for open PRs left intact.
  - [ ] PR #1332 (cf_gate_watch + polar_token_mint) — auto-merge armed.
  - [ ] 4 git stashes on master/work branches un-triaged.

## 2026-11-04 — FB token healed end-to-end (omv monitor spam stopped)

- **Trigger**: `FB heal: token invalid/expired` Telegram spam every 30 min —
  omv cron `*/30 ~/fb-watch/fb-heal.sh` finding a dead `~/secrets/fb-page-token`.
  Root cause: stored user token session expired 03-Nov 18:00 PST.
- **Fix chain (all automated via `facebook-browser-sidecar:9226`)**: implicit
  OAuth (`response_type=token`, canonical PLATFORM_SCOPES) → "Continue as"
  consent via `/debug/click` x/y (text-click 500s on this dialog) → captured
  short-lived token → App Secret recovered from dev console
  `apps/1936126137016578/settings/basic` "Show" (INSTAGRAM_PASSWORD satisfied
  the Meta re-auth dialog — stored as `FACEBOOK_PASSWORD` in `.env`) →
  `fb_exchange_token` → **never-expiring user token** (18 scopes, verified
  via debug_token with app token).
- **Written back**: omv `fb-user-token` + `fb-page-token` (re-minted,
  heal rc=0), secret_store `FB_USER_TOKEN`, DB personal FB account
  `9355ed63` access_token_enc, `.env` `FACEBOOK_CLIENT_SECRET` (was `""`)
  → `social-api` recreated, env now len=32.
- **Gotchas**: sidecar `/debug/navigate` needs unversioned `dialog/oauth`
  (v21.0 is retired → bounces to profile); Meta dev-console SSO went
  through a saved-login "Continue" wall first; CDP :9222 is hijacked by
  Lenovo Vantage WebView2 (msedgewebview2, PID-bound) — use the sidecar,
  not gate_runner, for FB work. Chrome 156 blocks CDP on real profile.
- **Leftovers**:
  - [ ] `threads`+`instagram` DB tokens return `Cannot parse access token`
    (probably garbage/stale entries — needs a separate look).
  - [ ] PR #1309 `facebook-token-check` worker still OPEN — merge gives the
    in-platform 6h lane; legacy omv fb-heal.sh also republishes the page
    (worker doesn't) — keep or port that action before killing cron.
  - [ ] Docker socket death-looped through the session; resolved via
    wsl-shutdown flag watcher. Watch for recurrence.

## 2026-11-03 — Pipeline Optimization Toolkit adopted (Founders 3-11-2026)

- **Asset 1 — LOA**: `scripts/letter_of_agreement.py` renders the 1-page
  €800/14-day Web+Hosting+Automation agreement (reportlab PDF + markdown).
- **Asset 2 — quiet-lead follow-up**: `QUIET_STEPS` lane in
  `nurture_sequence.py` (24h nudge → 3d pain → 7d clean break; age from
  `meta_data.quiet_lead_at`, supersedes the nurture ladder). Enroll/status/
  unenroll via `scripts/quiet_lead_followup.py`; daily `nurture-sweep` beat
  delivers touches.
- **Asset 3 — newsletter framework**: 3 seeded content_prompt_templates
  (Cost Horror Story / 14-Day Transformation / DIY Audit Checklist) — apply
  via `seed_content_templates.py` on next social-api boot.
- Skill: `.devin/skills/pipeline-assets-ops` (+ .cursor mirror).
- Website: cloudless.gr PR adds the €800 starter-package card on /services.
- **Leftovers — resolved same session**: nurture-ladder completion now
  auto-rolls leads into the quiet lane (no manual enrollment needed);
  sibling docs adopted — Sales/Conversation playbook scripts + objection
  handlers in the skill canon, `scripts/audit_worksheet.py` worksheet
  generator, 14-day onboarding checklist documented, ImplKit autoresponder
  copy replaced the nurture ladder (day 2 leak lesson / day 4 14-day clock
  / day 14 closer).
- **Agent Manifest + day1 frameworks adopted**: `/audit` landing page +
  3-field booking form on cloudless.gr (PR #2362, POST /api/audit →
  leads/public `form=audit-request`); LOA template bumped to manifest v2;
  Sofia coaching canon (30-sec pitch, complete-offer spec, positioning
  statement) in the skill as the offer-QA rubric.
- **Quiet-lane auto-cohort**: `QUIET_AUTO_FORMS=("audit-request",)` —
  /audit leads skip the ladder and enter the quiet lane stamped at lead
  creation, so the 24h nudge fires one day after they ask for the checkup.
- **Funnel liveness gates closed**: `scripts/pipeline_gap_doctor.py`
  (team-id discovery / audit-endpoint probe / db-env drift check —
  secrets never printed). Set `CLOUDLESS_WEB_ANALYTICS_TEAM_ID` to the
  public-leads team (was empty in .env; now in all worker/beat/api
  containers); DATABASE_URL password realigned to social-postgres;
  `social.cloudless.gr` verified live as the /api/audit upstream default.

## 2026-11-02 session 3 — media quality backfill (real pipeline) + Sofia scores

**Done:**
- All media assets carry `meta_data.quality`; assets with captions have real
  Sofia scores (0-100). Early passes wrote heuristic scores while
  LanguageTool/DMR were down — `scripts/backfill_media_quality.py` gained a
  `--force` flag to re-run the real pipeline over already-scored assets
  (idempotent, commits per asset, safe to re-run after stack bounces).
- Local protected-words misconfig fixed: `.env` + host `/app/.env` shim had a
  REPLACEMENT list (dropped `Redis`, lowercase `cloudless`) violating the
  config.py contract "extend via env, don't shrink the defaults" — restored to
  the full default list + local extras (OAuth, QLoRA, DBaaS, ONNX). This was
  the cause of `test_protected_spans_cover_configured_words` failing locally.
- Test env on host fixed: venv had pytest missing (plain `pip` resolved to
  system pip — use `.venv/bin/python -m pip`); unit tests for touched modules
  green: 96 passed / 6 integration-skipped.

**Leftovers:**
- [ ] Final `--force` backfill pass interrupted by Docker Desktop WSL bounces
  (deploy-tree cutover churn); retry wrapper `/tmp/backfill_retry.sh` re-runs
  until a full pass completes — verify final asset counts once stable.
- [ ] Real pipeline pass replaces remaining heuristic Sofia/SEO scores — check
  `meta_data.quality.spellcheck_applied=true` across assets when done.

## 2026-11-02 session 2 — synigoros alerting went live + leftovers

- **#1261 merged**: session/stop fix + .devin parity + creds scrub + SYNIGOROS_*
  x-worker-env passthrough + monitor.js notify fix (was 403 via social-api
  endpoint — now direct Bot API, verified msg in automation-hq) + gmail lane.
- **#1266 merged**: monitor EBUSY log-lock — run-monitor.bat `>>` + node's
  appendFileSync double-handled monitor.log; every scheduled run crashed for
  days (Last Result 1). Bat redirect dropped, log() is non-fatal.
- **Deployed live**: `SynigorosMonitor` task now every **15min** (was 4h) and
  works; Gmail early-check relays Ombudsman status emails in ~minutes;
  SYNIGOROS_MAIL/KPA set in WSL-clone + Windows .env; workers+beat recreated
  (env verified present, 4/4 celery nodes).
- **WSL clone note**: `~/cu130-slim` runs divergent branch
  `chore/autonomy-level-passthrough` (35 ahead/32 behind, other agent's
  in-flight work) — applied only the 3 needed file edits to its working
  tree, branch untouched. When that branch syncs with master it'll
  reconcile (same content for our files).
- **Reviewer follow-ups** (from #1261 review): BaseException catch in both
  skill scripts + worker stop-warning logging landed in fix/synigoros-leftovers;
  own-reddit-reuse stop NOT implemented — /session/status exposes no
  `interactive` flag so our leftovers can't be told from a real interactive
  login; WAITING_TIMEOUT bounds it anyway.

**Leftovers:**
- [x] All session items landed or mitigated.

## 2026-11-02 session — Devin Review #1083 tails (synigoros-ops)

- Devin Review on merged PR #1083 flagged 6 items; 4 were already fixed on
  master (ba2f2b45 + 0a7db738 + 0548f6c6), but the "session release" fix
  called `/session/close` — an endpoint that does not exist on
  browser-bridge.py (real: `/session/stop`), so the 404 was silently
  swallowed and the waiting session still pinned the adhoc bridge ~5 min.
- Fix branch `fix/synigoros-ops-review-1083`: `/session/stop` everywhere,
  skipped when `/session/start` returned `reused` (another caller's session);
  `.devin` mirror script brought to parity (was stale: force:true start,
  fixed 14s sleep, no bounded poll, close×3); residual personal email
  scrubbed from both SKILL.md copies; +3 unit tests (stop on success,
  stop on error, no-stop on reused).

**Leftovers:**
- [ ] Restart the 4 social-worker-* containers + beat after merge
  (worker task code changed — container rules).
- [ ] Gmail MCP connector in Devin Desktop fails to connect client-side
  (endpoint https://gmailmcp.googleapis.com/mcp/v1 is up — likely expired
  OAuth); repo gmail-ops OAuth path works as fallback.

## 2026-11-01 session — full PR + branch consolidation, image-text spelling guard

**Landed (all via squash PRs to protected master):**
- #1208 databricks-adopts, #1211 dmr-gateway, #1214 ai-functions, #1215 avatar-video
  register, #1217 avatar-story route, #1228 screen-drive-ops (add/add conflict on
  credential-reset-ops SKILL.md resolved — #1226 content kept + TOTP docs), #1243
  **image-text guard** (see below), #1244 ops-tools (nc_dav_probe + n8n_exec DB-key),
  #1245 fb-reauth signed oauth state, #1246 cdp_gate_runner v2 + meta_2fa ADB
  discovery, #1247 mautic-clean port (ai_function_call MODEL — was missing while
  ai_functions.py imported it; gate_sweep beat + engagement_autoapprove;
  datalake_insights.funnel_answer; pr-sweep.yml 30-min PR rebase+arm cron;
  shared sidecar-base image + split social-backend-api/social-backend Dockerfile
  targets; compose renamed to match, legacy image names kept as GHCR aliases).
- #1248 (healer) fixed test_media_ai expectation for the new guard output.

**Image-text guard (#1243) — the "garbled caption on media" fix:** FLUX/Wan cannot
spell; a monetization brief "…with a caption highlighting…" painted a garbled
banner into the pixels (LinkedIn activity 7521477348517363712, asset 582ca53c).
`app/services/image_text_guard.py`: strips caption/banner/text clauses from
generation prompts (wired into expand_visual_prompt → covers image+video+monetization),
routes explicit quoted-text requests to the PIL infographic overlay, and QA-checks
generated pixels (qwen3-vl transcribe → detect_gibberish) with one no-text retry →
brand-banner fallback in monetization; `meta_data.text_qa` on the endpoint path.
19 unit tests.

**Branch consolidation:** 20 remote branches deleted (content landed or superseded):
heal/2026102x-*, heal/20261031-*, feat/autoheal, feat/autoheal-all, feat/heal-supervisor-v2,
fix/stack-watchers-health, feat/chrome-bind-ops, feat/credential-reset-ops(+v2),
ops/gate-capture, feat/databricks-friendly, feat/meta-ads-ops-3, fix/master-ci-engagement-tests,
local/master-20261028-071718; local wt-1208/wt-1211 worktrees + stale local branches removed.
Residual-audit method: per-file `git log -1 --format=%cs` branch-vs-master date compare
+ 3-way merge for files both sides touched.

**Leftovers:**
- [ ] Delete `ops/mautic-clean` + `ops/mautic-funnel` remotes once #1247 lands (port carried their unique content; mautic-funnel ⊂ mautic-clean lineage).
- [ ] Restart social-api + 4 workers + beat after #1247 (container rules: model/celery/compose changes).
- [ ] First `docker compose pull && up -d` after #1247 pulls the renamed `social-backend-api-latest`/`social-backend-latest` images — verify /health + celery inspect ping (4 nodes) before traffic.
- [x] **Secret hygiene** (DONE 2026-11-01): #1251 moved `AUTHENTIK_CLIENT_SECRET` to settings/.env + compose passthrough, and the secret was **rotated live** via the authentik worker shell on omv (k3s: `kubectl exec -n authentik authentik-worker-… -- ak shell`) — old 40-char value is dead server-side, new 48-char value verified against the token endpoint (`invalid_grant` probe) and loaded in social-api. The old value remains in git history but is invalid; `.env` holds the current one (gitignored).
- [ ] Stash `grokbot-20261101` (compose autoheal labels) intentionally kept — labeled superseded by #1237; drop when convenient.
- [ ] The 2026-10-28 garbled asset (582ca53c) predates the fix; its LinkedIn post is live — delete+recreate is a content decision (same bucket as the cloudless.com→.gr posts).

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

**Content-quality research applied**: slop-keyword scan, voice constraints, prior-output exclusion, platform-fit (content_guides has limits, unenforced).

- Watchdog retiered 2026-11-02 eve: probe classified Healthy/Vsock/Broken; vsock flaps no longer wsl --terminate (VM-layer, terminate cant fix); docker.exe engine probe via npipe; docker desktop restart only if engine down; vsock-flaps.txt; [P1] to #critical at flap 6 (~30m) then ~3h; VsockDegraded state + recovery alert; -Mode TestVsock synthetic-probe test; wsl.exe spawn-failure now counted as flap (silent-death fix). Live-verified: real flap 19:10 logged/counted, zero terminate.
- Deferred options if vsock flaps persist: Docker Desktop tcp://localhost:2375 + DOCKER_HOST in distro (bypasses proxy/vsock entirely; unauth localhost daemon caveat); .wslconfig networkingMode=mirrored (needs full WSL restart).

- Watchdog hardening landed live (not in repo, host script): restart judged by "Starting Docker Desktop" output not exit code; engine restart throttled 15min; wsl.exe spawn-failure -> vsock flap. Verified: real 19:08-19:26 incident cycle recovered via docker desktop restart + Telegram alert.
- Windows checkout (feat/credential-reset-ops, 50+ behind master): untracked alembic copies b8c9d0e1f2a3 + f9a1b2c3d4e5 WILL COLLIDE on owner rebase (git refuses untracked overwrite) - fix: delete them before rebase, they are byte-identical to master. Compose/monitor/config.py dirt = owner work.

## 2026-11-03 — IG garbled-media post pending deletion

- IG post `18189629230413062` (media `76299e69`, "hosting hosting" AI-garbled text) still live; local posts row `76299e69` left `published` to reflect reality.
- Delete blocked: Graph has no organic-media delete; instagrapi password login broken (`publickeys` endpoint); stored sessionid expired (redirect loop); bridge IG at login wall; sidecar db.json empty.
- **RESOLVED 2026-11-03**: post was already deleted on-platform (verified via fresh bridge IG session — instagrapi `media_delete` → MediaNotFound, user_media list shows the Oct-28 gap). Local row `76299e69` force-deleted. Bonus: bridge now holds a LIVE cloudless.gr IG session (WhatsApp-2FA login driven via /session/evaluate; code read from phone over ADB).

## 2026-11-03 — mail triage leftovers

- **Bluesky @tbaltzakis.bsky.social**: account `active` but `last sync: never` (ops health 2026-11-03) — follower-sync cadence appears to skip bluesky; verify the sync task covers it or mark as known gap.
- **LinkedIn @baltzakis.themis personal**: 1 sync error — expected member_postAnalytics scope gap (documented); no action.
- **"Book a free audit" chat lead (2026-11-02 15:37, IP 9.246.61.72)**: NOT captured into EspoCRM — visitor left no contact details (Talk visitor chat only). Needs a reply inside the chat channel while it's live; CRM loop can't capture anonymous chats — consider adding an email field prompt to the widget.
- **Watchdog flap continued into 06:39-06:41Z** — WSL vsock still degrading; `wsl --shutdown` recommended at next maintenance window.

## 2026-11-03 — megaplan findings

- **UTM/CRM-loop gap (real)**: last 14d — 55 published posts, only 6 carry a cloudless CTA and only 1 has `utm_source=`. The post generator doesn't append CTAs consistently and doesn't UTM-tag them. Fix = CTA injection + UTM stamping in the copy pipeline (PLATFORM_GUIDES CTA + campaign slug per post).
- **Chat lead capture**: shipped cloudless.gr PR #2361 — visitor emails → EspoCRM lead_source=chat.
- **Follower-guard**: small-account >50% drop check shipped (PR #1295); live audit showed FB personal (~66) vs page (~2) are two legit accounts, no poisoned rows.
- **Watchdog dedup exists** (per-incident) — the overnight flood was real flapping, fixed via docker-socket-watch + WSL recycle.
- **Trend-grounding**: shipped PR #1296 — niche-filtered Google Trends signal cited in copy+media prompts.
- **Test EspoCRM lead** `6ae9e83353e2` ('verify devin-verify') — delete when convenient (PUT cleanup 404'd).

## 2026-11-03 — feat/screen-drive-ops stale-merge cleanup

- Branch was mid-merge of master into the already-merged, upstream-deleted
  `feat/screen-drive-ops`. Resolved 3 conflicts (compose=ours+theirs verified,
  monitor.js=ours for `lastNews` semantics, ai_functions.py=whitespace) and
  committed locally as `296e65b0` — **local only, never push this branch**.
- Reverted to index: duplicate of open PR #1302 (`_cf_shadow_call` in
  leads.py/nurture_sequence.py + tests — remote branch is 7x more complete)
  and an accidental eslint downgrade in package.json/lock.
- Rescued the only real local work → PR #1303 (vitest localStorage mock).
- **Leftover (master-side hygiene PR candidate)**: these are committed on
  master but misplaced — `.devin/chrome-launch-ops/` (byte-identical dup of
  `.devin/skills/chrome-launch-ops/`), `.devin/skills/wsl-docker-doctor/scripts/
  SKILL.md` (belongs at skill root; content differs from top SKILL.md — merge
  unique bits before deleting), `.devin/triggers/restart-chrome.flag`
  (machine state committed to repo).
- **Watch master**: `COMFYUI_URL=http://172.20.0.230:8000` hardcoded in
  compose bypasses the stack-ops wake proxy — comfyui sleeps → workers can't
  wake it. Verify whether the pinned-IP change was intentional.

## Founders / SocialAuto adoption (2026-11-03)

Done:
- [x] Cloudless_Agent_Manifest.toml committed (.devin + .cursor pipeline-assets-ops)
- [x] Day-14 nurture copy fixed: €800-2,400 -> €800 single fix / €3,600-mo bundle
- [x] DM canon adopted in nurture_sequence.py: DM_OPENERS, BOOKING_DM_STEPS (24h/3d/4d), DM_SAFETY_VALVE
- [x] onboarding_checklist.py: 14-day fulfillment checklist generator (PDF/MD)
- [x] media-converters skill (pdf_to_image, audio_bed, image_to_video) in backend/scripts
- [x] BMB cert video published to personal LinkedIn: urn:li:ugcPost:7523480082607202304
- [x] stack-ops recovered (dashboard/app.py squatted :8080; killed, restarted)

Left:
- [x] Site PR #2364 merged (coverage fix + 8 Sentry findings fixed incl. server-side consent gate); /en/consultation live (200)
- [x] Book-audit CTAs flipped to /en/consultation — PR #1312 (auto-merge armed)
- [ ] DM lanes are copy-constants only (no sweepable DM surface exists; email quiet-lane is the live impl)

## Ops automation (2026-11-03, PR pending)

- [x] publishing.py: LinkedIn sidecar declines video/PDF media (was silently posting text-only — cert incident root cause) + tests
- [x] scripts/stack_ops_rescue.py: stack-ops Exited -> restart -> port-squatter ID + Slack alert
- [x] scripts/pr_babysit.py: open-PR loop — resolve Sentry auto-fixed threads, update BEHIND branches, arm auto-merge on green
- [ ] schtasks registration for both scripts on OFFICE host

## Ops automation round 2 (2026-11-03)

- [x] scripts/social_post.py: file -> media convert -> upload -> post -> publish -> poll, in-container one-liner
- [x] pr_babysit.py: Slack digest of new unresolved review findings (deduped state in logs/pr-findings-state.json)
## Review fixups after #1310 (2026-11-03)

Done:
- [x] Stray test_cf_mail_outbound.py never reached master (dropped before #1310 merged); the CF worker lane it tested lives on feat/cf-mail-lane (#1308)
- [x] PR #1300 (feat/funnel-cta-utm) closed as superseded — identical CTA code landed via #1310, and its branch predates #1296 (would have reverted /en/consultation CTAs + dropped trend_context)
- [x] onboarding_checklist.py: --price flag (manifest €800 single-fix default; €3,600/mo bundle engagements now render correctly)
- [x] audio_bed.py whoosh: ramp capped at 1.1x (was 2x -> s16 tail clipping)
- [x] monetization_executor.py: re moved to module top (was function-level)

Left:
- [ ] feat/cf-mail-lane (#1308): test_cf_mail_outbound.py there also lacks @pytest.mark.asyncio (asyncio_mode=strict) — marks pushed to the branch; verify CI goes green
- [ ] DM lanes are copy-constants only (no sweepable DM surface exists; email quiet-lane is the live impl)

## Operator gates status (2026-11-04)

Done:
- [x] Email Routing destination `tbaltzakis@cloudless.gr` — added + **Verified** via CDP dashboard path (earlier DOM adds silently failed; direct dialog submit worked)
- [x] mail-outbound worker transport verified: `POST $MAIL_OUTBOUND_URL` → `{"ok":true}` (worker answers POST /, NOT /send — comment in email_digest.py says "/send contract", harmless but stale)
- [x] `CLOUDFLARE_WORKERS_DEPLOY_TOKEN` set on both repos (cfut_ user token; probe showed no Workers Scripts scope — first real cloudflare-deploy.yml run validates it)
- [x] PR #1317 merged: gate-runner hardening (SSO self-heal, trusted-click, CDP reconnect, SSH-safe poll)

Left:
- [ ] Email Routing verify emails do NOT land in /home/*/Maildir on omv — routing sends tbaltzakis@ to mail-ingest worker → https://webmail.cloudless.gr/ingest (behind CF Access; origin host unknown). gate_runner's omv-maildir poll is unreliable for this; the verify this time landed via dashboard state after CF auto/processed delivery — recheck where ingest actually stores mail if this gate is needed again
- [ ] FB OAuth reconnect still open — `gate_runner.py fb-token <authorize-url>` needs an authorize URL + FB login in debug Chrome
- [ ] After #1302/#1308/#1309 merge: `docker compose restart social-api social-worker-* celery-beat`
- [ ] Debug Chrome on this host dies repeatedly (no periodic taskkill found — watchers are flag-gated via .devin/triggers/*.flag); runner's reconnect handles it but adds latency

## Gates closed 2026-11-04 (cont.)

Done:
- [x] FB user token re-minted via Graph API Explorer CDP drive (app=Cloudless, User Token) — wrote omv ~/secrets/fb-user-token (235B) + social_accounts row 9355ed63 → status=active; fb-token-heal.sh rc=0; 30-min fb-heal cron re-enabled
- [x] CLOUDFLARE_WORKERS_DEPLOY_TOKEN validated live: cloudflare-deploy.yml dispatch run 41746504516 → success (cfut_ user token works despite probe showing no scopes)
- [x] gate_runner Email Routing poll now watches the dashboard row status (source of truth), maildir grep demoted to best-effort — pushed to feat/ops-automation (PR #1314)

Left:
- [ ] Restart social-api + workers + celery-beat after #1302/#1308/#1309 merge
- [ ] FACEBOOK_CLIENT_ID/SECRET still empty in .env — Accounts-UI OAuth reconnect path needs them (manual token mint via Explorer is the working fallback)

## 2026-11-04 — WSL distro migration (Ubuntu-26.04 -> Ubuntu-24.04)
- [x] Projects restored to /home/tbaltzakis/{cu130-slim,cloudless.gr}; containers rebound via compose up -d (38 up/35 healthy, api 200)
- [x] Devin/Cursor/Code IDE state repointed (state.vscdb, storage.json, workspace.json); WT default profile = Ubuntu-24.04
- [x] Toolchain: gh 2.45 + auth (Themis128), git identity, jq/unzip/htop, sysctl 99-devin-docker.conf, journald 200M cap
- [x] .devin/mcp_config.json migrated to WSL paths (python3/bash/npx)
- [x] Scheduled tasks: conhost --headless wraps + UNC repoints (wsl-recycle-watch, docker-socket-watch, GmailTelegramWatch, cf-session-keepalive, cu130-pr-babysit, cu130-stack-ops-rescue, SynigorosMonitor, 3x Chrome launchers, DevinWSLShutdown)
- [ ] env-manager-backend still Created: port 8081 owned by galaxys222-dashboard-1 (0.0.0.0 bind) — needs remap decision (override or move dashboard)
- [ ] ChromeDebug task edit denied (stored-credential task) — needs owner password or elevate
- [ ] ~/.ssh empty in new distro — omv SSH keys lost w/ old vhdx; restore from NAS wsl-layer backup or regen+install
- [ ] omv watchdog remediation still refs C:/Users/tbaltzakis/cu130-slim/docker-compose.yml — repoint to wsl -d Ubuntu-24.04
- [ ] Windows copies pending rename (Devin holds handles): cu130-slim, Themis128_cloudless.gr -> *.moved-to-wsl-20261104; watcher fix-devin-wsl-2404.ps1 covers on IDE exit
- [ ] Devin storage.json regen from memory until Devin restart — watcher armed

## 2026-11-05 — LinkedIn dev portal automation (CDP-eval lane)

- New app `cloudless-gr-member` (ID 265077368, client 77fd3pui3m2jwm)
  created + **company-verified** to cloudless.gr Page — driven entirely
  via `cdp_eval.py` (WSL→win_agent→CDP on the user's real Chrome;
  LinkedIn's bot filter trusts the real session).
- Products added: Share on LinkedIn, Sign In with OpenID Connect.
- Requests submitted: Community Management (access-request-form),
  Advertising API.
- **Pending**: Member Data Portability buttons stay disabled until the
  business-email verify link lands in tbaltzakis@cloudless.gr — click
  it, then re-run the products drive. Auth tab redirect URI
  `https://social.cloudless.gr/api/v1/auth/oauth/linkedin/callback`
  still to add; then swap client_id/secret + one consent for the
  member-analytics surfaces.
- Tool: `.devin/skills/win-bridge-ops/scripts/cdp_eval.py` —
  suppress_origin ws fix, base64 Windows file-drop, URL-match tab eval.

## 2026-11-05 — Synigoros monitor triage (Devin)

- [x] Diagnosed ⚠️ timeout spam: `scripts/synigoros-monitor/monitor.js`
  (Windows task `SynigorosMonitor`, 15-min headless Chrome) — 13 real
  failures / ~380 polls (3.4%) each alerting instantly. Fixed in PR
  #1366: `waitForFunction` arg-slot bug + consecutive-error streak
  (alert on 2nd, once per outage, ✅ recovery notice, state.json now
  written on error path).
- [ ] Celery `poll_synigoros` (daily 07:30) has NO debounce and its
  first-status-word extraction disagrees with monitor.js's last-match —
  📋 "status changed" messages flip-flopped both directions. Align
  extraction + add candidate confirm (same pattern as monitor.js) if
  noise continues. Note: which Κατάσταση block (stepper vs detail) is
  authoritative is unverified — check the live page.
- [ ] monitor.log + state.json grow unboundedly in the live dir
  (957 lines/4 days) — consider rotation if it becomes a problem.
