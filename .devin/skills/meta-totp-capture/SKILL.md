# meta-totp-capture — own the Meta authenticator, kill the 2FA gate

Captures the Meta/Facebook TOTP secret into `.env` (`META_TOTP_SECRET`)
while a live session exists, so every future Facebook login is
unattended. Sister to `totp-ops` (X). The hard part — getting a session
— is already done via `phone-2fa-ops` (phone-read WhatsApp code → CDP).

## How

With a logged-in debug-browser session (clone profile + `:9222` debug +
`cdp_fwd` `:9223`):

1. Open `accountscenter.facebook.com/password_and_security/two_factor`
2. Drive "Add authenticator app" → Meta shows a `otpauth://` link / QR
3. Extract the `secret=` param → `META_TOTP_SECRET` in `.env`
4. `pyotp.TOTP(secret).now()` generates codes on demand — wire into the
   consent_healers credentials-login path (same as X's `TWITTER_TOTP_SECRET`)

## Script — `scripts/meta_totp.py`

```
python3 meta_totp.py --status    # is a secret stored + does the code verify?
python3 meta_totp.py --capture   # drive the enrollment on the live session
python3 meta_totp.py --code      # print the current code
```

## Why this matters

The 2026-10-29 login needed a phone-pull of the WhatsApp code — works,
but fragile (phone must be reachable + code rotates fast). A stored TOTP
secret removes the phone from the loop entirely.
