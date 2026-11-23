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

## Case chat lane — `scripts/meta_case_chat.py`

Stdlib + `websocket-client` only — runs from WSL against the debug Chrome
(`:9229`, auto-resolved via the WSL gateway) or any CDP endpoint
(`CDP_URL` override; `:9330` sidecar fallback). Real mouse clicks via
`Input.dispatchMouseEvent` — Meta auth surfaces ignore `el.click()`.

```
python3 meta_case_chat.py login       # FB Meta Account re-auth end-to-end
python3 meta_case_chat.py login2fa    # 2FA page -> TOTP (META_TOTP_SECRET_AC)
python3 meta_case_chat.py cases       # "Your support cases" list + status
python3 meta_case_chat.py threads     # maiba chat-thread list
python3 meta_case_chat.py read [substr]   # dump case-chat transcript tail
python3 meta_case_chat.py send "msg" [--wait 120]  # reply + poll for response
python3 meta_case_chat.py watch [substr] [--interval 300]  # TG-alert on new msgs
```

`login`/`login2fa` encode the full 2026-11-23 recovery chain:

1. Cookie banner → "Allow all cookies" (blocks LS-NONCE otherwise).
2. Logged-out profile picker → real-click **Continue** → lands on
   `recover/initiate` "Choose a way to log in".
3. `[role=radiogroup]` = the "Continue with password" option → click it,
   then **Continue**.
4. Password field is `input[type=password]` with NO name attr; the working
   credential is the **secret-store `FACEBOOK_PASSWORD`** (16 chars) —
   `.env`'s 20-char value is stale and gets "password incorrect".
   `_fb_password()` prefers the store for exactly this reason.
5. Log-in button must be clicked INSIDE the password field's `<form>` —
   the footer "Log In" link matches the same regex and silently no-ops.
6. 2FA page (`two_step_verification`): push "Was this you?" may auto-clear
   on a trusted device; otherwise `login2fa` → "Try another way" →
   "Authentication app" → TOTP. Backup codes (`META_BACKUP_CODES`) and
   WhatsApp/SMS (+**38) are the other lanes.
7. "Invalid request / could not validate" on `two_step_verification` =
   expired `encrypted_context` — restart the flow from step 1, don't reload.

Case-chat reality: the specialist thread opened by "Go to chat"
(`account_quality_hub_support_case_item_button_open`) has **no**
`maiba:thread_list_item` — that surface belongs to the Meta AI assistant
pane. `read`/`send` auto-open the case pane and use the "Write a message"
box when no thread items exist.

## Case-ID reality

Case numbers DO surface in Business Support Home → "Your support cases"
once a specialist chat exists (e.g. `2037307673624450`, opened 2026-11-23).
`meta_case_chat.py cases` lists them; the numeric ID also appears in the
case item's `data-surface`. AI-assistant-only reviews may still leave the
list empty — the artifact of record is then the transcript, archived in
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
