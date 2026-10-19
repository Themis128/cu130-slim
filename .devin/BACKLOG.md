# Backlog — leftover work across sessions

Living list. **Every agent session must read this at start and update it at end** —
check off what landed, add anything left unfinished, and keep items verbatim enough
to act on (IDs, URLs, file paths). Rules: `.devin/rules/` + `AGENTS.md`.

## Ops — infra health

- [ ] **LanguageTool `stack-ops:8010` is down (503)** — gibberish/auto-correct checks
  fail open (advisory, publishing unaffected). Restart the service; verify
  `curl http://stack-ops:8010/v2/check` responds. Seen 2026-10-19.
- [ ] **Slack publish notify broken** — webhook 404 → bot-token fallback →
  `channel_not_found`. Stale channel config; fix via `slack-ops` skill /
  `scripts/slack_tool.py`. Seen 2026-10-19.
- [ ] **Watch Postgres restarts** — one mid-publish restart ~15:14 EEST 2026-10-19
  left an orphaned `processing` queue lock (manually reset). If it recurs,
  investigate the recreate trigger + add stale-lock sweeper.
- [ ] **DMR cold-start + no CLI fallback** — DMR API offline on 2026-10-19;
  `docker CLI not found` inside `social-worker-publishing`. NLP fell back
  gracefully, but DMR-dependent generation stalls during cold starts.
  Still live 19:50 EEST: `generate-content` → `httpx.ConnectError:
  Temporary failure in name resolution` (500). When DMR is back, re-run the
  Oxford AIE template smoke test (EU AI Act template, id
  `474a19ee-5a85-4fb9-a9a9-788a89cb6b6f`).
- [ ] **Influencer/partner surface — unbuilt** (Prakoso et al. 2024 rec. 3):
  no watchlist/outreach tracking exists. If adopted, needs a real surface
  (Nextcloud table or CRM list) — not a mock.
- [ ] **`warp-proxy` unreachable** — instagrapi SOCKS path down (seen 2026-10-19);
  private-API IG verification blocked. Check the warp container.
- [ ] **X/Twitter + TikTok token expiry** — Grafana token panel showed X ~1h and
  TikTok ~1.5h to expiry on 2026-10-19. Confirm the refresh sweep renewed them;
  if not, `session-ops`.

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

- [x] Ops report 2026-10-19 triaged: LinkedIn "failure" = historical noise;
  Facebook post rewritten (Sofia NLP 100, was 75) + requeued → **published**
  <https://www.facebook.com/116436681562585_122244667814100600>
- [x] Prometheus per-platform metrics + Grafana `Social Platforms` dashboard
  (PR #736 merged `b8f99e40`); EspoCRM exporter + `EspoCRM Vitals` dashboard
  live in k3s.
- [x] OMV apticron: `openmediavault-omvextrasorg` 8.0.4 + `openmediavault`
  8.5.12-1 applied over Tailscale.
- [x] LinkedIn newsletter funnel: 168 subscribers → EspoCRM leads, PR #735 merged.
