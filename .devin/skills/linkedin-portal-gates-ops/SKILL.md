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

## Sidecar lane — `li_portal_sidecar.py` (2026-11-13)

When the :9229 debug-Chrome session is dead but the :9225 sidecar still
holds a logged-in LinkedIn session, the portal gates can be probed
through the sidecar's `/debug/navigate` + `/debug/eval` HTTP surface —
no CDP relay needed. Runs inside `social-api` (compose DNS resolves
`linkedin-browser-sidecar`):

```bash
~/scripts/li-portal-sidecar.sh --json     # host wrapper → docker exec
# or in-container: python /dev/stdin < scripts/li_portal_sidecar.py --json
```

- Read-only by default; reports `loggedIn`, product rows, and the page
  snippet for app `265077368` (override with `--url`).
- `--drive` best-effort clicks the Member DP "Request access" row and
  dumps the modal — same stop-at-inputs rule as `member_dp_probe --drive`.
- **Never pass `--force` on a schedule**: `/session/clear-rate-limit`
  re-arms the circuit with a longer backoff on the next redirect loop
  (observed 2026-11-13: force during cooldown extended `rate_limit_until`
  ~5h). The script refuses to navigate while `/health` shows
  `rate_limited` — let the circuit cool.
- Cron: `*/30 * * * * ~/scripts/li-portal-sidecar.sh --json >>
  ~/logs/li-portal-sidecar.log` — self-gating, no-ops during cooldown.
- Once the circuit clears, a logged-in probe + `--drive` covers the same
  Member DP request flow the CDP lane does; Matched Audiences case
  status can be read via `--url` on the app's Support page.

## Grant watcher — `li_grant_watch.py` (2026-11-14)

Detects product-grant transitions by probing the gated APIs with the stored
tokens (inside social-api) — ground truth, not portal labels:

```bash
python3 scripts/li_grant_watch.py            # probe + diff + TG alert + auto-dispatch
python3 scripts/li_grant_watch.py --json     # machine-readable, no side effects
python3 scripts/li_grant_watch.py --status   # last recorded state
```

- Probes: `GET /rest/dmpSegments?q=account&account=<ad urn>` (Matched
  Audiences, org token), `GET /rest/memberSnapshotData?q=criteria` +
  `memberChangeLogs` (Member DP, person token, **Linkedin-Version 202312**,
  `q=criteria` required — bare GET 404s on version/params, not authz),
  plus `token-inspect` scope sets per account.
- On transition to granted → TG `#ops` alert + auto-dispatches
  `sync_linkedin_dmp_audiences` / `sync_linkedin_portability` so the lane
  starts immediately instead of waiting for the daily beat.
- Verified live 2026-11-14: dmpSegments → 403 ACCESS_DENIED (correct),
  memberSnapshotData → 403 ACCESS_DENIED (correct), member analytics scopes
  already live.
- Cron: `7 */2 * * *` user crontab → `~/logs/li-grant-watch.log`.
- **Re-consent caveat**: even after LinkedIn approves a product, the member
  token must be re-authorized to carry the new scope (e.g.
  `rw_dmp_segments`). The watcher alerts on "scope in token but API still
  denies" — run the `linkedin-reauth-ops` lane then.

## Matched Audiences appeal — `li_vetting_appeal.py` (2026-11-14)

Files a case through the official form `linkedin.com/help/linkedin/ask/dsapi`
(the Developer Support Request Form — primary channel since 2026-05-15,
Zendesk sunset). Fields: Application Client ID, Form Type (Vetting Appeal),
API Program, Description. Client IDs for all three apps are baked in.

```bash
python3 scripts/li_vetting_appeal.py --dump     # inspect the live form
python3 scripts/li_vetting_appeal.py --fill     # fill, don't submit
python3 scripts/li_vetting_appeal.py --fill --submit
```

Cheaper first step: check `li_product_state.py 264925843` for a **Reapply**
button — declined/expired requests resubmit from the portal directly.
Appeal copy rules baked into the script: precise verifiable facts only,
never overstate the legal entity (reviewers cross-check), real ΑΦΜ/ΓΕΜΗ
only if the user supplies them.

## Member DP request — full flow already exists

`linkedin-devapp-ops/scripts/li_devapp_ops.py request 265077368 "<heading>"`
runs the complete modal session: Request access → business-email verify →
6-digit code via `mail-code-fetch/fetch-code.py` → confirm → ToS → Request
access. Headings on 265077368: Member DP (Member) / Member DP (3rd Party).
Needs the :9229 portal session — armed, fires post-cooldown.

## Rules

- Never submit legal/business-info forms with fabricated data — the
  `cloudless-code-of-honor` (real numbers only) applies; leave placeholders
  for the owner.
- Portal session must be the `cloudless-gr-member` dev account — the
  probe prints which profile is logged in before touching anything.
- Matched Audiences: the denial may need Greek business registration
  (ΑΦΜ/ΓΕΜΗ) if support asks — that document step is irreducibly human.
