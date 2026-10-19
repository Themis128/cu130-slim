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
- [x] **`/api/v1/ai/generate-content` exceeds ~280s** — FIXED 2026-10-20
  (PR #754). Root cause: `score_brand_compliance._ai_tone_match` hardcoded
  `provider_name="dmr"` (~72s local call behind the DMR slot) regardless of
  the requested provider, and `chroma add_content` was awaited inline (~18s).
  Provider now passes through; chroma index write is a background task; the
  frontend `generateContent` wrapper defaults to `provider='cloudflare'`
  (matching `generateImage`). Live-verified: CF path 95.5s (was >300s
  timeout). DMR path remains the free/local fallback for n8n/celery callers
  on the docker network (no gateway timeout there).
- [ ] **Influencer/partner surface — unbuilt** (Prakoso et al. 2024 rec. 3):
  no watchlist/outreach tracking exists. If adopted, needs a real surface
  (Nextcloud table or CRM list) — not a mock.
- [x] **TikTok `url_ownership_unverified` — RESOLVED** (verified 2026-10-20
  via post_targets): the only occurrences are 2026-09-16/17, predating the
  `cloudless.gr` domain verification (done 2026-10-02, tiktok-console-ops).
  No recurrence since. Remaining TikTok publish blockers are different
  classes: `unaudited_client_can_only_post_to_private_accounts` (Direct Post
  audit under review), `spam_risk_too_many_pending_share` (inbox drafts must
  be finished in the app), and `MEDIA_PUBLIC_BASE_URL` unset when the tunnel
  is down — none are code fixes.
- [ ] **Facebook Page `Cloudless.gr` status=revoked** — healer confirmed
  2026-10-19 (`accounts_synced: 3`, this page stays revoked). Needs user
  OAuth re-connect in the UI; not a session-heal case.

- [x] **`warp-proxy`** — recreated; `warp=on` verified 2026-10-19.
- [x] **X/Twitter + TikTok token expiry** — TikTok self-healed (23.6h); X was
  refreshed by the token sweep 2026-10-19 (manual refresh verified → 20:17 UTC).

## Open PRs / merges to verify

- [x] **PR #737** — IG Graph permalink pk→shortcode fix + swept payloads.
  MERGED 2026-10-19.
- [x] **PR #741** — WhatsApp lead-gen Flow promotion + `send_flow` param fix.
  MERGED.
- [x] **PR #742** — AIE email-course drip sequence. MERGED; workers
  (`social-worker-default`, `celery-beat`) restarted to load the drip steps.
- [x] **cloudless.gr PR #2228** — `/links` live social proof. MERGED
  2026-10-20 (subscriber count via `Contact/leadSource=Email`,
  `unstable_cache` for the dynamic route, conditional attribution).
- [ ] **PR #754** — generate-content provider pass-through + cloudflare
  default for interactive callers. Auto-merge armed.
- [x] **PR #739** — skills sync merged `2026-10-19T12:48Z`.
- [x] **PR #740** — Oxford AIE docs merged (`5c1d2ff6`).

## Blocked on humans / consoles

- [ ] **Meta `leads_retrieval` App Review** — paid lead-ad delivery gated;
  blocked on Meta business verification. Webhook code is armed on both paths.
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
  **Verified 2026-10-19:** this IS the funnel's `lead→nurtured` break —
  EspoCRM has 179 leads (171 New / 8 Dead); only 3 have an email and all 3
  are self/test entries (owner's own contact-form submissions), correctly
  skipped by the drip. The newsletter leads carry LinkedIn profile URLs in
  `description` — LinkedIn DM via the sidecar is the only reachable nurture
  channel. Email nurture cannot move this cohort.
- [ ] **EspoCRM: `LinkedIn newsletter` TargetList missing** — the 168 leads
  exist (source=Newsletter) but the list vanished; `Social engagement
  (replies)` shows entryCount 168 — investigate whether members were
  mis-attributed or the list was deleted/recreated.

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
- [x] **Post-merge worker restart** — done 2026-10-19:
  `social-worker-default` + `celery-beat` restarted; #742 drip steps loaded.

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
