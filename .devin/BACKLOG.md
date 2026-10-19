# Backlog — leftover work across sessions

Living list. **Every agent session must read this at start and update it at end** —
check off what landed, add anything left unfinished, and keep items verbatim enough
to act on (IDs, URLs, file paths). Rules: `.devin/rules/` + `AGENTS.md`.

## Ops — infra health

- [ ] **Regenerate Slack incoming webhooks** — ALL webhook URLs 404
  (`no_service`, Slack app likely reinstalled). Bot-token fallback verified
  working 2026-10-19 (postMessage ok → #socialauto), so notifications still
  land — but webhooks need re-creation in the Slack app console (user action)
  and `.env` refresh. Via `slack-ops` / `slack_tool.py webhooks`.
- [ ] **`/api/v1/ai/generate-content` exceeds ~280s** — pipeline is 4-6
  sequential DMR calls (generate → plain-English rewrite → quality loop ×2);
  DMR answers trivial prompts in ~25s (both `ai/llama3.2` and
  `Qwen3-4B-Instruct-2507-GGUF:Q4_K_M`) but the full pipeline times out the
  client and once killed a uvicorn worker. DMR itself is ONLINE
  (`check_dmr_health` ok, keep-warm cycling). Real fix options: stream/chunk
  the pipeline, raise client+gateway timeouts, or route UI generation to the
  cloudflare provider path. Re-test the Oxford AIE template
  (`474a19ee-5a85-4fb9-a9a9-788a89cb6b6f`) once addressed.
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

## Open PRs / merges to verify

- [ ] **PR #737** — IG Graph permalink pk→shortcode fix (`fix/ig-graph-permalink-shortcode`).
  Verify authoritative merge state; once merged the Deck cards + datalake
  `platform_url`s are correct going forward.

## Blocked on humans / consoles

- [ ] **Meta `leads_retrieval` App Review** — paid lead-ad delivery gated;
  blocked on Meta business verification. Webhook code is armed on both paths.
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

## Growth / funnel

- [ ] **Oxford AIE adoption — bigger pieces** (templates landed 2026-10-19):
  (a) 4-week "AI Act in 4 weeks" email course — needs a *sequence* engine
  (current drip engine sends one template/wave, not offset series);
  (b) AI-governance/audit service framing on `cloudless.gr/pricing|/contact`
  — user decision; (c) "EU AI Act readiness checklist" lead magnet on
  `/links` (second magnet beside the 12-Automation checklist).
  Source: `Kakkava Sofia/Oxford/AIE_Brochure_May2026.pdf`.
- [ ] **Bio links → `/links`** — most profiles still point at the homepage;
  switching IG/FB bios to `cloudless.gr/links` closes Meta playbook #2
  (profile → lead capture). Was offered to user, not yet approved.
- [ ] **Social proof on `/links`** — add checklist download count + one
  testimonial (Meta playbook #5).
- [ ] **Promote the WhatsApp `cloudless_lead_gen` Flow** — live and published on
  the WABA, responses land as SocialAuto leads → `WhatsApp leads` list, but
  nothing advertises it. Send it in WA replies/onboarding.

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
