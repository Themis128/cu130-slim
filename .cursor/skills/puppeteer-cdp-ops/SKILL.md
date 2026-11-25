# puppeteer-cdp-ops — Puppeteer driver for human-act browser lanes

Puppeteer-core driver for the `:9229` debug Chrome (and any CDP endpoint).
Use this for **human acts** — login/recovery/2FA/consent flows on
facebook.com, linkedin.com, etc. — instead of hand-rolled
`websocket-client` scripts. `page.click()`/`page.type()` emit **trusted
CDP Input events**; Meta/LinkedIn ignore `el.click()` and synthetic
JS events (burned in from `fb_recover_drive.py`).

## Tool — `scripts/pdrive.js`

```bash
cd .devin/skills/puppeteer-cdp-ops/scripts
node pdrive.js targets                        # list page targets
node pdrive.js text --match facebook.com      # body innerText
node pdrive.js eval "<js expr>" --match linkedin.com
node pdrive.js click "Get code via email" --match facebook.com
node pdrive.js fill "input[name=email]" "user@x.com" --match facebook.com
node pdrive.js type "input[name=pass]" "secret" --match facebook.com  # real keys
node pdrive.js shot /tmp/fb.png --match facebook.com
node pdrive.js run ./my_act.js --match facebook.com   # multi-step script
node pdrive.js open https://… --match facebook.com    # nav existing tab
```

Env: `CDP_URL` overrides endpoint discovery. Candidates:
`192.168.96.1:9229` (Windows relay, WSL lanes), `localhost:9229`,
`127.0.0.1:9229`.

## `run` script contract

`module.exports = async (page, browser, { cdpBase, match }) => { … }`

`page` is a puppeteer Page already bound to the matched tab.

## Gotchas (live-verified 2026-11-25)

- **Multiple same-site tabs**: `--match` sorts by URL length — the
  canonical tab wins over deep flow tabs (`/hacked/disavow`,
  `/two_step_verification/…`). Pass a longer `--match` substring to pin
  a specific flow tab.
- **Click text matching**: matches `innerText`/`textContent` +
  `aria-label`, case-sensitive `includes`; `--exact` for full equality.
  Icon-only buttons (FB hamburger) need the aria-label string
  (e.g. `"Facebook menu"` not `"Facebook Menu"`).
- **React inputs**: `fill` uses the native value setter + input/change
  events (same pattern as fb_recover_drive); `type` sends real keystrokes
  for fields that ignore value sets.
- **Relay flakiness**: the :9229 WS relay drops mid-navigation
  (`Target closed`, `Connection lost`). Every command reconnects fresh —
  just retry. Never hold a WS open across a navigation.
- **FB feed is heavy**: first eval after load can take 10-20s; the
  heal scripts' `_wait_eval_live` pattern applies — poll `1` until alive.

## Session lanes this replaces/augments

- `fb_recover_drive.py` / `fb_session_heal.py` — raw-WS CDP; keep for
  cron healers, use pdrive for interactive human-act drives.
- `li_session_heal.py`, `member_dp_probe.py` — same CDP base; pdrive
  `--match linkedin.com` reaches the same tabs.
- SMS/OTP reads stay on `termux-sms-list` over phone SSH (:8022) — see
  `phone-2fa-ops`.

## Deps

`puppeteer-core@24` in `scripts/package.json` — no browser download;
drives the existing Chrome. `npm install` once per checkout.
