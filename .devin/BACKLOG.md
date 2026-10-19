# Backlog — leftover work across sessions

Living list. **Every agent session must read this at start and update it at end** —
check off what landed, add anything left unfinished, and keep items verbatim enough
to act on (IDs, URLs, file paths). Rules: `.devin/rules/` + `AGENTS.md`.

## Ops — infra health

- [x] **LanguageTool `stack-ops:8010`** — container was being deleted repeatedly;
  recreated via `docker compose up -d languagetool` → 200, real grammar matches.
  **Watch**: if it vanishes again, find what's removing it (external prune?).
- [x] **Slack publish notify** — `SLACK_PUBLISHING_CHANNEL_ID` mapped to api +
  workers (docker-compose). Live delivery verified in `#socialauto`
  (`C0BT263L17U`). Dead webhook 404 remains cosmetic (env var unused path).
- [ ] **Regenerate Slack incoming webhooks** — ALL webhook URLs 404
  (`no_service`, Slack app likely reinstalled). Bot-token fallback verified
  working 2026-10-19 (postMessage ok → #socialauto), so notifications still
  land — but webhooks need re-creation in the Slack app console (user action)
  and `.env` refresh. Via `slack-ops` / `slack_tool.py webhooks`.
- [ ] **Watch Postgres restarts** — one mid-publish restart ~15:14 EEST 2026-10-19
  left an orphaned `processing` queue lock (manually reset). Queue verified
  clean 2026-10-19 (0 processing rows; 288 completed / 1 failed). If it
  recurs, investigate the recreate trigger + add stale-lock sweeper.
- [ ] **`/api/v1/ai/generate-content` exceeds ~280s** — pipeline is 4-6
  sequential DMR calls (generate → plain-English rewrite → quality loop ×2);
  DMR answers trivial prompts in ~25s (both `ai/llama3.2` and
  `Qwen3-4B-Instruct-2507-GGUF:Q4_K_M`) but the full pipeline outlives client
  timeouts (verified 2026-10-19, dmr + cloudflare providers) and once killed
  a uvicorn worker. DMR itself is ONLINE (`check_dmr_health` ok). Fix
  options: stream/chunk the pipeline, raise client+gateway timeouts, or
  route UI generation to the cloudflare provider path. Re-test the Oxford
  AIE template (`474a19ee-5a85-4fb9-a9a9-788a89cb6b6f`) once addressed.
- [ ] **Influencer/partner surface — unbuilt** (Prakoso et al. 2024 rec. 3):
  no watchlist/outreach tracking exists. If adopted, needs a real surface
  (Nextcloud table or CRM list) — not a mock.
- [ ] **TikTok publish blocked: `url_ownership_unverified`** — failed
  post_target 2026-10-19: PULL_FROM_URL requires verified domain
  (`social.cloudless.gr` or equivalent). Fix via `tiktok-console-ops`:
  domain verify in TikTok for Developers (DNS TXT via Cloudflare).
- [ ] **Facebook Page `Cloudless.gr` status=revoked** — healer confirmed
  2026-10-19 (`accounts_synced: 3`, this page stays revoked). Needs user
  OAuth re-connect in the UI; not a session-heal case.
- [x] **`warp-proxy`** — recreated; `warp=on` verified 2026-10-19.
- [x] **X/Twitter + TikTok token expiry** — TikTok self-healed (23.6h); X was
  refreshed by the token sweep 2026-10-19 (manual refresh verified → 20:17 UTC).

## Open PRs / merges to verify

- [ ] **PR #737** — IG Graph permalink pk→shortcode fix + swept payloads
  (skills sync, WA flow code, compose env). OPEN, BEHIND master, auto-merge
  armed. Note: overlaps PR #741 — whichever merges first, the other's diff
  shrinks; identical hunks merge clean.
- [ ] **PR #741** — WhatsApp lead-gen Flow promotion + `send_flow` param fix.
  Auto-merge armed (CI pending).
- [ ] **PR #742** — AIE email-course drip sequence. Auto-merge armed.
- [ ] **cloudless.gr PR #2228** — `/links` live social proof (EspoCRM count +
  AppFlowy testimonial). OPEN, CI pending.
- [x] **PR #739** — skills sync merged `2026-10-19T12:48Z`.
- [x] **PR #740** — Oxford AIE docs merged (`5c1d2ff6`).

## Blocked on humans / consoles

- [ ] **Meta `leads_retrieval` App Review** — paid lead-ad delivery gated;
  blocked on Meta business verification. Webhook code is armed on both paths.
<<<<<<< HEAD
- [ ] **LinkedIn newsletter outreach — awaiting user decision** — 168 subscribers
  enrolled in EspoCRM (`LinkedIn newsletter` campaign/list). Options put to user:
  (1) warm-up DM drip 5–8/day to 146 message-able, (2) invite-first to the
  Connect/Follow slice, (3) manual assist. Also undecided: first message = free
  30-min audit vs lighter opener.
  **Verified 2026-10-19:** this IS the funnel's `lead→nurtured` break — EspoCRM
  has 179 leads (171 New / 8 Dead); only 3 have an email and all 3 are
  self/test entries (owner's own contact-form submissions), correctly skipped
  by the drip. The newsletter leads carry LinkedIn profile URLs in
  `description` — LinkedIn DM via the sidecar is the only reachable nurture
  channel. Email nurture cannot move this cohort.
=======
- [ ] **WhatsApp phone registration** — `+30 697 777 7838` is
  `code_verification_status: NOT_VERIFIED` → Cloud API sends 133010
  "Account not registered". **SMS code needed**: request_code currently
  rate-limited (136024/2388091); when the user can receive the SMS, run
  verify-code + register (whatsapp-ops scripts). Flow promotion is armed and
  will activate automatically once registered.
- [ ] **LinkedIn org profile write** — `rw_organization_admin` scope missing;
  re-auth needed to set company-page website/about.
- [ ] **Instagram bio → `/links`** — website/name edits are mobile-app-only
  (hard platform limit; do not force API/sidecar paths).
- [ ] **TikTok bio/website** — writes captcha-blocked.
- [ ] **LinkedIn newsletter outreach — awaiting user decision** — 168
  subscribers in EspoCRM. Options: (1) warm-up DM drip 5–8/day, (2)
  invite-first, (3) manual assist. Undecided: audit CTA vs lighter opener.
- [ ] **EspoCRM: `LinkedIn newsletter` TargetList missing** — the 168 leads
  exist (source=Newsletter) but the list vanished; `Social engagement
  (replies)` shows entryCount 168 — investigate whether members were
  mis-attributed or the list was deleted/recreated.
>>>>>>> origin/master

## Growth / funnel

- [x] **Bio links → `/links`** — FB Page website + Threads bio updated
  2026-10-19 (IG/TikTok/LinkedIn blocked — see above).
- [x] **Social proof on `/links`** — live-count + featured-testimonial block,
  cloudless.gr PR #2228.
- [x] **WhatsApp Flow promotion** — `_maybe_promote_flow` armed: greeting +
  business intents → `cloudless_lead_gen` (1971236133545424), 7-day resend
  cap. Activates once number registers (see blocked).
- [x] **Oxford AIE email course** — sequence engine + 4 seeded drip_template
  campaigns (`aie_course_1..4`), 7-day steps, PR #742.
- [ ] **AI-governance audit service page** on `cloudless.gr/pricing|/contact`
  — user decision (positioning, pricing).
- [ ] **"EU AI Act readiness checklist" lead magnet** on `/links` — needs PDF
  + second `ChecklistSignup` card (monthly-checklist-update skill).
- [ ] **Post-merge worker restart** — after #742 lands: checkout master, pull,
  `docker compose restart social-worker-default celery-beat` so the new drip
  steps load (bind-mounted code).

## Done recently (reference)

- [x] **Backlog infra triage 2026-10-19 (via platform/slack/n8n MCPs):**
  LanguageTool back up (200 `/v2/languages`, container restarted);
  `warp-proxy` + `facebook/tiktok/messenger` browser sidecars were stopped →
  started, all health 200, SOCKS5 open (instagrapi path restored);
  Slack bot-token publish path verified (`ok:true` → #socialauto);
  `publish_queue` clean — no orphaned `processing` locks (288 completed /
  1 failed); `refresh_expiring_tokens` beat verified, X token manually
  refreshed → new expiry 20:17 UTC; session-heal sweep clean
  (`unhealthy: []`, contended = busy not dead); analytics sync errors →
  `errors: []` once sidecars up (follower_snapshots is a change-log —
  writes only on delta or 7-day heartbeat, so "staleness" is flat counts).
  DMR online — see generate-content latency item.
- [x] Ops report 2026-10-19 triaged: LinkedIn "failure" = historical noise;
  Facebook post rewritten (Sofia NLP 100, was 75) + requeued → **published**
  <https://www.facebook.com/116436681562585_122244667814100600>
- [x] Prometheus per-platform metrics + Grafana `Social Platforms` dashboard
  (PR #736 merged `b8f99e40`); EspoCRM exporter + `EspoCRM Vitals` dashboard
  live in k3s.
- [x] OMV apticron: `openmediavault-omvextrasorg` 8.0.4 + `openmediavault`
  8.5.12-1 applied over Tailscale.
- [x] LinkedIn newsletter funnel: 168 subscribers → EspoCRM leads, PR #735 merged.
- [x] Oxford AIE brochure → 4 `ContentPromptTemplate`s live + verified via
  `/generate-content` (SEO 91).
