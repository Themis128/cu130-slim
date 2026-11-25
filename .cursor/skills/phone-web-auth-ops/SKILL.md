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
more" take the **Meta Account password** — canonical since 2026-11-24,
user directive: never change it. The value lives in Devin memory
(`MEMORY.md`) and the host secret store — NEVER in this repo. It covers
all linked profiles (FB, both IGs, Threads, Meta AI) and is a DIFFERENT
credential than the IG-native password — an IG-native form
(`username+password`, no Meta Account card) will reject it with
"login information is incorrect". Legacy passwords are dead — never
try them.

## Lane 3 — instagram.com → threads.com SSO bridge (2026-11-24 evening revision)

The raw OIDC endpoint does NOT work standalone: any invented `state`
lands on Threads' "Not all who wander" dead page — the exchange
validates state server-side. The working recipe:

1. **Clear stale threads.com cookies first** or the old session gets
   silently restored instead of the SSO completing. `Storage.clearDataForOrigin`
   is unsupported on Android Chrome — use per-name `Network.deleteCookies`
   (sessionid, ds_user_id, csrftoken, ig_did, mid, dpr, datr, fr, sb) on
   `.threads.com` AND `.threads.net`.
2. Load `https://www.threads.com/login/` — the page HTML embeds
   `"oidc_url":"https:\/\/www.instagram.com\/threads\/sso\/?oidc_uri=..."`
   with a REAL single-use state. Extract with
   `html.find('"oidc_url":"')` then decode the field as JSON:
   `json.loads('"' + raw + '"')` — this handles BOTH the `\u0025` unicode
   escapes and the escaped slashes `\/` (plain `unicode_escape` leaves
   the backslashes in).
3. Navigate the SAME tab to that `oidc_url` — lands on
   `instagram.com/threads/sso/` showing "Continue to Threads — <profile>"
   cards for every IG profile in the session's multi-login.
4. Click the target profile card (`div[role=button]` containing the
   handle — JS .click() works here) → OIDC → exchange →
   `threads.com/` logged in.

**RESOLVED — the mint "wrong-profile" mystery is documented linkage.**
AGENTS.md (verified live 2026-11-16): the brand IG `cloudless.gr` is
linked to the Threads profile **`@t_baltzakis`** (SocialAuto row
`c0c6bf77`, account_id `38255289647418861` — the ACTIVE one to re-auth).
The old `@cloudless.gr` Threads profile **no longer exists**
(threads.com 404 even logged in) — its row `1071dcd5` is correctly
`revoked`. Do NOT try to mint a cloudless.gr Threads username. So the
cloudless.gr IG session minting a t_baltzakis Threads session is the
CORRECT resolution, not a bug. The Threads app showing "cloudless.gr"
in its composer reflects the linked IG identity label — the profile
that posts IS t_baltzakis. Web threads.com has NO profile switcher —
log out fully (cookie wipe above) to change identity.

**Card taps vs JS clicks**: tapping the card with real pixel input
fires an `intent://` that the IG APP intercepts — the app completes
SSO natively and the THREADS APP (com.instagram.barcelona) gets the
session (verified: cloudless.gr live in-app). That does nothing for
the web session. For a web session, click via CDP Runtime.evaluate
in the same tab.

Once `threads.com/` shows the right profile's feed:

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

## Tool — `scripts/phone_ig_cdp.py`

Second-generation CDP driver focused on the IG/OAuth lanes (trusted
Input.dispatchTouchEvent taps — JS .click() does NOT fire Bloks/React
handlers; React native-setter fill without doubled text; foreground
tab probing). `list / probe / eval / nav / tap-text / fill-login /
consent / code / cookies`. Runs on the Windows host with
`adb forward tcp:9222 localabstract:chrome_devtools_remote`. Port is
env-configurable: `PHONE_CDP_PORT=9555` — 9222 is also used by the
desktop debugging relay (cdp-bind-pattern), so forward the phone onto
a free port when both run.

## Gotchas

- Threads app CANNOT consume OAuth authorize URLs — opens feed. OAuth
  consent exists only as a web surface (documented Meta design).
- The "Use your Instagram account" embedded Bloks login on
  threads.com/login is a DEAD END for sessioned accounts — the
  continue-as card inside it fires an intent to the IG app. Use the
  `oidc_url` extraction recipe (Lane 3) instead.
- ntfy/user activity steals foreground constantly — verify
  `topResumedActivity` before pixel taps; recover with
  `monkey -p com.android.chrome -c android.intent.category.LAUNCHER 1`.
- reCAPTCHA may gate the Meta Account submit — one tap on the checkbox.
- `keyevent 66` (Go) submits focused forms; avoid `keyevent 4`.
- Threads OAuth authorize `state` is account-scoped: mint a fresh URL
  per account via `meta-oauth-ops/scripts/mint_authorize.sh threads`.
