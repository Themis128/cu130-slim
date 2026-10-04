# Cloudflare Account Abuse Protection — applicability to this stack

Assessment of the [Account Abuse Protection (AAP) Dashboard]
(https://blog.cloudflare.com/account-abuse-protection-dashboard/) (announced
2026-10-02) against our two Cloudflare-connected surfaces, with the concrete
steps to adopt it once available to us.

## What AAP is (one paragraph)

AAP shifts account-takeover defense from stateless point-in-time checks to a
**stateful model**: Cloudflare cryptographically hashes an identifier you
already send on every login/signup (email, username, or phone) into a
privacy-preserving **Hashed User ID**, then accumulates login history,
failed logins, leaked-credential matches, unique IPs/devices, country/ASN and
time-of-day patterns per account. The dashboard is an investigation funnel —
population overview → filtered cohorts (e.g. ≥3 failed logins + leaked
credential match + ≥5 unique IPs) → per-account timeline with Ray IDs — and
the action loop ends in account recovery and/or a **WAF rule matching the
Hashed User ID** to challenge or block.

## Availability — the honest part

- **Bot Management Enterprise, Early Access** (signup form). Our zones are
  not Enterprise today, so the dashboard itself is **not actionable yet**.
- Everything below is preparation so the switch-on is a config change, not a
  project.

## Which of our surfaces AAP would actually see

| Surface | Through Cloudflare edge? | AAP coverage |
|---|---|---|
| cloudless.gr `/api/auth/*` (D1 auth, opaque session cookies) | yes — proxied | ✅ primary candidate |
| cloudless.gr signup/login pages | yes — proxied | ✅ |
| SocialAuto API (`social-api:8083`) | **no** — docker-compose, localhost/LAN only | ❌ invisible to AAP unless we put a proxied public hostname in front |
| Media view endpoint (MEDIA_PUBLIC_BASE_URL / Cloudflare tunnel) | yes — tunnel | ⚠️ scrapers/abusers of public media are visible as edge traffic, but there is no login identifier on those requests |

## Preparation checklist (no Enterprise required)

1. **Keep the login identifier stable.** AAP hashes whatever identifier our
   login/signup flow sends. Both surfaces already send `email` in the auth
   POST body — do not "optimize" it into device-bound or rotating values.
2. **Do not block bot traffic on the login route at the edge.** AAP learns
   from failed logins; aggressive `Block` rules on `/api/auth/*` starve the
   model. Prefer `Managed Challenge` for anomalies.
3. **Role plan (when EA is granted):** assign `Account Abuse Protection` to
   the ops team; `Account Abuse Protection PII` only where the raw email is
   needed (it is also required for Logpush jobs containing PII).
4. **Logpush readiness:** decide the destination (R2 — we already ship ETL
   there) for `account-abuse` datasets; the PII role is required to create
   those jobs.
5. **Response runbook (works today, AAP later):**
   - Triage funnel mirror: our own auth logs (D1 + SocialAuto) already record
     failed logins per account — the same cohort queries (failed ≥3, ≥5
     unique IPs) run as SQL today.
   - On a confirmed compromise: force session invalidation (D1 `sessions`
     delete / SocialAuto token reset), then require password reset.
   - When AAP is live: additionally apply a WAF custom rule on the Hashed
     User ID (`cf.bot_management.accounts_abuse_huid`-style field per docs)
     → `Managed Challenge`.

## Signup

Bot Management Enterprise customers: Early Access form in the Cloudflare
dashboard. Non-Enterprise: the same form registers interest — revisit after
any plan upgrade.
