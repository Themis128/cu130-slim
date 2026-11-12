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

## Rules

- Never submit legal/business-info forms with fabricated data — the
  `cloudless-code-of-honor` (real numbers only) applies; leave placeholders
  for the owner.
- Portal session must be the `cloudless-gr-member` dev account — the
  probe prints which profile is logged in before touching anything.
- Matched Audiences: the denial may need Greek business registration
  (ΑΦΜ/ΓΕΜΗ) if support asks — that document step is irreducibly human.
