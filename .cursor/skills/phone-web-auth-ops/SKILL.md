---
name: phone-web-auth-ops
description: Passwordless + session-bridge auth lanes on the user's Galaxy S22+ for Meta platforms — phone-Chrome CDP drive, the instagram.com→threads.com OIDC SSO bridge (no Threads password needed), the one-click emaillogin + backup-code recovery lane, and the Meta Account password gate. Use for Threads/IG OAuth re-mints, Meta logins where the IG-native password is unknown, or any web auth the user approved on the phone.
---

# phone-web-auth-ops — auth lanes via the user's phone

Proven live 2026-11-24: minted `t_baltzakis` Threads `active` end-to-end
without any IG-native password — emaillogin link → backup code →
instagram.com session → OIDC bridge → threads.com session → OAuth consent.

**Scope**: interactive drives the user approved for the phone only.
Phone rules (AGENTS.md): Meta/TikTok flows go through official apps;
the phone browser is used ONLY for web-only surfaces the apps can't
host (OAuth consent pages) and only when the user okays it.

## Tool — `scripts/phone_cdp.py` (Windows host, adb + websocket-client)

```bash
python phone_cdp.py forward            # adb forward tcp:9555
python phone_cdp.py tabs               # list pages
python phone_cdp.py new <url>          # fresh FOREGROUND tab — always works
python phone_cdp.py nav <substr> <url> # navigate a tab
python phone_cdp.py eval <substr> '<js>'  # Runtime.evaluate
python phone_cdp.py shot <substr> out.png # CDP screenshot
python phone_cdp.py front              # thaw background tabs
```

Key facts:

- CDP endpoint: `adb forward tcp:9555 localabstract:chrome_devtools_remote`
  → `http://127.0.0.1:9555/json/list`; ws needs `suppress_origin=True`.
- **Android Chrome suspends CDP on background tabs** — evaluate/click on a
  frozen tab hangs forever. `Target.createTarget` (browser-level ws from
  `/json/version`) creates a foreground tab that always answers.
- `Runtime.evaluate`/`Page.navigate` may drop the reply mid-redirect —
  poll `/json/list` for the landing URL instead of blocking on recv.
- Phone taps are REAL pixels (1080x2340); screencaps return 724x1568
  (scale ≈1.49). `input text` needs `%XX` escaping (`!` → `%21`).
- Google Password Manager autofill sheets steal taps mid-typing —
  tap outside to dismiss, re-verify the field took the text.

## Lane 1 — one-click emaillogin + backup codes (IG web session)

When the IG-native password is unknown but the account is logged into
the IG APP on the phone:

1. In Chrome: `instagram.com/accounts/password/reset/` → "Find by email
   or username" → username → Meta may send a "get back in" email.
2. Read the mailbox with `mail-code-fetch` (`doveadm` over SSH on the
   Windows host; decode quoted-printable bodies) — extract the
   `/_n/web_emaillogin?uid=...&token=...` link.
3. Open the link → lands at 2FA. "Try another way" → Backup code.
4. Backup codes are in the IG APP (logged in): Accounts Center →
   Login and security → Two-factor → Backup codes. Screenshot the list;
   each code is single-use (format `NNNN NNNN`, submit without space).
5. Fill via React native setter + input event, Continue →
   logged-in instagram.com session.

- Code delivery to the user's +30 697 777 7838 is BROKEN (WhatsApp+SMS
  never arrive — filed escalation meta-2fa-codes). Always go straight to
  the Backup code lane; don't wait on delivery.
- `password/reset/confirm` links may render blank AND kill the pending
  2FA tab — prefer the emaillogin link.

## Lane 2 — Meta Account password gate

Instagram flows that hit "Log into your Meta Account — <profile> and N
more" take the **Meta Account password** (`TH!123th!` — verified live
2026-11-24), which covers all linked profiles (FB, both IGs, Threads,
Meta AI). This is a DIFFERENT credential than the IG-native password —
an IG-native form (`username+password`, no Meta Account card) will
reject `TH!123th!` with "login information is incorrect".

## Lane 3 — instagram.com → threads.com OIDC bridge

With a logged-in instagram.com session, log threads.com in WITHOUT any
Threads/IG password:

```
python phone_cdp.py new "https://www.instagram.com/oauth/oidc/?app_id=1289884158313322&redirect_uri=https%3A%2F%2Fwww.threads.com%2Flogin%2Foidc%2F&response_type=code&scope=openid&state=<any>&force_consent=0&force_authentication=0"
```

(`app_id=1289884158313322` = Threads' OIDC client on Instagram. The real
`state`+`logger_id` come from a threads.com login attempt — captured URL
pattern `instagram.com/accounts/login/?next=.../oauth/oidc/...`; extract
the `next` param, or reuse the constant form above.)

The OIDC redirects through `threads.com/login/oidc/` and lands on
`threads.com/web/` logged in. Then:

```
python phone_cdp.py nav threads.com "<threads.net/oauth/authorize URL from mint_authorize.sh>"
```

→ GDPR "You previously connected" screen → tap **Continue As <user>** →
`social.cloudless.gr/.../threads/callback?code=...` → row flips `active`.

## Lane 4 — account switching on instagram.com

Profile page → ⌄ next to the handle opens "Add account" (an IG-native
login form — NOT Meta-Account linked switching). To mint a second
profile's Threads row, repeat Lane 1 with that profile's reset email +
backup codes, OR run the Meta Account gate (Lane 2) which can log the
whole linked account in.

## Gotchas

- Threads app CANNOT consume OAuth authorize URLs — opens feed. OAuth
  consent exists only as a web surface (documented Meta design).
- reCAPTCHA may gate the Meta Account submit — one tap on the checkbox.
- `keyevent 66` (Go) submits focused forms; avoid `keyevent 4`.
- Threads OAuth authorize `state` is account-scoped: mint a fresh URL
  per account via `meta-oauth-ops/scripts/mint_authorize.sh threads`.
