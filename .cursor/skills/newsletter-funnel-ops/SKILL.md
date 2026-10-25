# Newsletter Funnel Ops — subscriber → connection → audit → proposal

Owns the LinkedIn-native nurture loop for newsletter subscribers — the
audience leg of the agency funnel. Companion to `linkedin-newsletter-ops`
(which owns the voyager subscriber sync itself).

## The loop

1. **Newsletter issue publishes** (weekly) — notifies all subscribers,
   carries real quote + sourced links + cover (linkedin_ai authority fields)
2. **`newsletter-subs-sync`** (Sun 09:15) pulls NEW subscribers → EspoCRM
   Leads tagged `source: "LinkedIn newsletter"` + `LI-URN:` dedupe marker
3. **Welcome touch** — each new subscriber posts an ops-channel alert with
   name/headline/profile link + a ready welcome note → owner connects/DMs
4. **Connect → DM opens** — 1st-degree connection unlocks the LinkedIn
   inbox (the link messenger already replies to inbound DMs)
5. **Issue CTA → `/contact` → free-audit** — captures the EMAIL, flipping
   the lead from LinkedIn-only to the email nurture sequence (day 3/7/14)
6. **Audit → proposal → deal** — the agency pipeline takes it from there

## Scripts

- `scripts/funnel-stats.sh` — live funnel counts: subscribers → CRM leads
  → audits → proposals. The health check for the whole loop.
- `scripts/welcome-pending.sh` — which subscribers hit the ops channel
  this week and haven't been touched (from Redis synced-URNs).

## Guards

- **No email → no email nurture** — subscribers get LinkedIn-native
  touches ONLY (connect note, DM, issue CTA). Once they hand over an
  email at `/contact` they flip into `nurture_sequence` (form=free-audit).
- **DMs stay AI-replied, human-approved** — the messenger answers inbound
  threads; a proactive DM needs owner approval first (autonomy=review).
- **Connect rate limits** — LinkedIn caps ~20 invites/day; the welcome
  touch is a suggestion, not auto-sent — owner connects from the profile
  link in the alert (one tap).
