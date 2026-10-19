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
- [ ] **Watch Postgres restarts** — one mid-publish restart ~15:14 EEST 2026-10-19
  left an orphaned `processing` queue lock (manually reset). If it recurs,
  investigate the recreate trigger + add stale-lock sweeper.
- [ ] **`/api/v1/ai/generate-content` exceeds ~280s** — pipeline is 4-6
  sequential DMR calls (generate → plain-English rewrite → quality loop ×2);
  DMR answers trivial prompts in ~25s but the full pipeline outlives client
  timeouts (verified 2026-10-19, both dmr and cloudflare providers). Fix
  options: stream/chunk the pipeline, raise client+gateway timeouts, or
  route UI generation to the cloudflare provider path. DMR itself is online
  (watchdog `/api/ps` probes still flaky — monitor).
- [ ] **Influencer/partner surface — unbuilt** (Prakoso et al. 2024 rec. 3):
  no watchlist/outreach tracking exists. If adopted, needs a real surface
  (Nextcloud table or CRM list) — not a mock.
- [x] **`warp-proxy`** — recreated; `warp=on` verified 2026-10-19.
- [x] **X/Twitter + TikTok token expiry** — TikTok self-healed (23.6h); X was
  refreshed by the token sweep 2026-10-19.

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
