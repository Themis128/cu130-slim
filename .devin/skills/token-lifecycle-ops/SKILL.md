---
name: token-lifecycle-ops
description: Proactive token/session expiry management — the refresh-ahead pattern (per-provider lead times, rotating-refresh locking, reauth_required classification, early warning for non-refreshable credentials). Use when tokens die between sessions, a sweep catches an expired account, or deciding refresh vs reauth.
---

# Token lifecycle ops

Pattern adopted from industry practice (social token refresh architecture):
**never wait for a 401** — refresh on schedule with per-provider lead
times, refresh on-demand right before use, lock so only one refresher
redeems a rotating refresh token, and treat `reauth_required` as an
expected state with an escalation link — not an error.

## Provider lead times (refresh model)

| Provider | Lead | Model |
|---|---|---|
| TikTok | 4h | 24h access, **rotating** refresh — lock, never parallel |
| Instagram | 10d | 60d long-lived token self-extends (min 24h old) |
| Facebook user | 14d | extends via app secret; page tokens never expire |
| LinkedIn | 7d | refresh token only with MDP partner grant — else reauth |
| X | 7d | refresh only with `offline.access` scope |
| WhatsApp | — | permanent once verified; re-verify = human gate |

## Tooling

- `scripts/token_expiry_sweep.py [--dry-run]` — scans `SocialAccount`
  meta `expires_at`/`token_expires_at`, buckets entries into
  `warn` (inside lead, refresh lane covers it) vs `reauth` (expired on
  a non-refreshable provider → Slack escalation). Add to beat as an
  hourly task or run manually.
- Existing healers (`consent_healers`, `token_refresh`,
  `facebook_token_check`) do the reactive path — this script is the
  scheduled-ahead half the pattern requires.

## Non-refreshable = human gate, by design

LinkedIn (no MDP), X (missing offline.access), WhatsApp re-verify, and
platform consent clicks CANNOT self-heal. The sweep escalates them to
Slack with the reauth lane (`pending-inputs-ops`, `consent-root-ops`,
`x-oauth-wire`, `whatsapp-ops`) instead of retrying — classify first,
then either refresh or escalate, never both blindly.

## Rules

- Store `expires_at` on every token write — no expiry, no sweep.
- Rotating refresh tokens (TikTok): one refresher only; two parallel
  redeems burn the new token and disconnect the account.
- Refresh ~quarter of lifetime early; refresh reactively before a
  publish that would 401.
- `reauth_required` is a state with a link, not a log error.
