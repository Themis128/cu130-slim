---
name: linkedin-portal-gates-ops
description: >-
  Remaining LinkedIn developer-portal human gates — Member DP (Member)/(3rd
  Party) in-portal "Request access" modals on app 265077368, Matched Audiences
  support-case status on 264925843, and the CM Standard-tier Qualtrics survey.
  member_dp_probe.py checks the live portal session over CDP and reports
  exactly which gate is open; --drive makes a best-effort modal attempt.
  Tool: scripts/member_dp_probe.py.
---

# LinkedIn portal gates

Three gates live inside LinkedIn's own surfaces — no API exists. All are
driven through the debug Chrome CDP session (`:9229` relay → Windows
`chrome-debug-profile`, per `consent-root-ops`/`cdp-bind-pattern`).

| Gate | Where | Human remainder |
|---|---|---|
| Member DP (Member) | app `265077368` → Products → Request access modal | business-email verify click inside modal |
| Member DP (3rd Party) | same | legal/business info + privacy policy + email verify |
| Matched Audiences | app `264925843` product | support case (denied "business verification failed") — check case status in portal Support |
| CM Standard Tier | Qualtrics `SV_3Q1SbLSZoImyw6O` | ~2 min screen recording w/ US-English audio — see `cm-survey-ops` for post-recording automation |

## Probe

```bash
python3 scripts/member_dp_probe.py            # session + portal state
python3 scripts/member_dp_probe.py --json     # machine-readable
python3 scripts/member_dp_probe.py --drive    # best-effort modal attempt
```

The probe navigates the LinkedIn tab to the app page, dumps every
"Request access"/product row it can see, and reports per-product state.
`--drive` clicks the Member DP (Member) request button and prints the
modal text — stop there if it demands inputs automation can't supply.

## Portal session self-heal — `li_session_heal.py`

The :9229 LinkedIn session dies periodically (cookie expiry, checkpoint,
account restriction). The healer keeps it alive:

```bash
python3 scripts/li_session_heal.py status    # logged-in? jar state?
python3 scripts/li_session_heal.py seed      # pull sidecar-synced cookies from DB
python3 scripts/li_session_heal.py capture   # store live cookies after a manual login
python3 scripts/li_session_heal.py heal      # cookie-inject -> credential-login fallback
python3 scripts/li_session_heal.py daemon    # poll + heal + deduped TG alerts
```

Heal order: stored CDP cookie jar (`~/.local/state/li-portal-session/`,
0600) → auto-seed from `social_accounts.meta_data.browser_storage_state`
(kept fresh by session_healer when the :9225 sidecar is logged in) →
`LINKEDIN_USERNAME/PASSWORD` credential login (bails on Cloudflare
challenge / captcha / checkpoint → human-needed + deduped TG #ops alert).

**Self-renewal (2026-11-13):** when the jar holds the `li_rm` +
`bcookie` pair, cookie-heal also mints a FRESH `li_at` — LinkedIn issues
one automatically when `/feed/` loads with the pair present (verified
mechanic, no password/checkpoint). Heal polls the jar ~24s for the mint,
then re-captures rotated cookies. A single manual login + `--capture`
therefore self-heals indefinitely; the credential-login fallback only
matters if the pair itself is revoked.

**2026-11-12 status:** the account is under a LinkedIn restriction —
sidecar reports circuit open until 2026-11-13 (`linkedin_account_
restriction_2026-11-12`), stored cookies are rejected, and /login sits
behind a Cloudflare challenge. Automation is armed; it self-heals the
moment a valid session exists (one manual login → `--capture` →
auto-heal thereafter).

## Rules

- Never submit legal/business-info forms with fabricated data — the
  `cloudless-code-of-honor` (real numbers only) applies; leave placeholders
  for the owner.
- Portal session must be the `cloudless-gr-member` dev account — the
  probe prints which profile is logged in before touching anything.
- Matched Audiences: the denial may need Greek business registration
  (ΑΦΜ/ΓΕΜΗ) if support asks — that document step is irreducibly human.
