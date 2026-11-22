---
name: meta-support-ops
description: Drive Meta Business Support escalations end-to-end — contact-support wizard + Meta AI business assistant over headless CDP, per-issue case text, transcript/screenshot archive to the meta-support-comm repo. Use when the user asks to "escalate to Meta", file a support case for a restriction/portfolio/WABA issue, or push Meta communication to git. Pair with meta-oauth-ops (token/session reauth) and meta-account-lock-ops (checkpoint gates).
---

# Meta Support Ops

Automated escalation of Meta Business/accounts issues through
`facebook.com/business-support-home/contact-support` plus the **Meta AI
business assistant** chat, driven over the headless CDP browser on the
Windows host (`:9330`). One drive per issue; every run archives the full
transcript + final screenshot.

## Tool

`scripts/fb_escalate.py <issue_key>` — runs on the **Windows host** (Playwright
over `connect_over_cdp("http://localhost:9330")`), not in WSL or a container.
Cookies come from the FB sidecar first:

```bash
# on Windows (git-bash / pwsh):
curl -s http://localhost:9226/debug/all-cookies > /c/Users/tbaltzakis/devin-scripts/fb-cookies.json
python fb_escalate.py personal-restriction
```

Issue keys (edit `ISSUES` in the script to add more):
`personal-restriction`, `portfolio-mutation`, `dead-ad-accounts`,
`waba-system-user`.

## Gate playbook — what the drive handles and what it can't

| Gate | Handling |
|---|---|
| Cookie/session dead | Transplant from sidecar `:9226` `/debug/all-cookies` into the :9330 context before `goto` |
| "Switching accounts — you need to switch to …" interstitial | Click the `[role=button]` profile card (NOT the `<a>` — that navigates to the public profile), then "Continue", then re-`goto` the wizard URL. Persists across runs → **real human gate**: one manual "Continue as Themistoklis" clears it for subsequent unattended runs |
| React combobox that ignores `fill`/typing | Set via the native value setter + `input`/`change` events (see `page.evaluate` in `drive()`) |
| "Describe issue" single field | This wizard wants the WHOLE case text in the one search-style input, not a multi-step asset picker |
| Wizard → AI assistant | After the Next chain, consent gate ("Continue"/"Get started") then `div[role=textbox]` |

`snap_text()` retries through ExecutionContext-destroyed navigations so
transcripts survive mid-poll navigations.

## Case-ID reality

Meta's wizard/AI lanes **do not mint a case number** unless a human specialist
queue picks it up (live chat). The "Your support cases" list stays empty on
AI-only reviews — the artifact of record is the transcript, archived in
`escalations/<key>.log` + `<key>.png`. If a numbered case is required, drive
until the specialist-transfer offer appears or use the manual wizard path.

## Archive

Working transcripts land in `C:\Users\tbaltzakis\devin-scripts\fb-escalations\`;
canonical archive repo: https://github.com/Themis128/meta-support-comm
(`escalations/` + `case-record.md` + `transcripts/`). Bundle local files
(`tar -czf`), `local_copy` to the VM, extract into `escalations/`, commit.

## Current standing issues (2026-11-22)

1. Personal account `1134463867` — Feb-2021 advertising restriction, no review
   button; lift path = `facebook.com/id/hub` identity confirm (manual).
2. Portfolio `1558125105019725` — Business Settings mutations blocked
   (post-password protective window + downstream of #1).
3. Ad accounts `657781691826702` + `2771400643144250` — permanently dead
   (Jan-2021, review window expired); path = create a NEW ad account.
4. WABA `1717901383017723` — needs System User in portfolio (blocked by #1/#2)
   to mint the never-expiring WhatsApp API token.
