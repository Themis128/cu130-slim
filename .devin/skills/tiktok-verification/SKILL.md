# tiktok-verification

Resubmit the rejected TikTok business registration for @cloudless.gr
(Baltzakis Themistoklis, AFM 115307716) via the Business Center web path —
the phone app's Business Suite is a WebView that ignores ALL injected input
(coordinate taps, uiautomator2 a11y clicks, sendevent, CDP socket — verified).

## Proven login path (2026-10-27)

The app webview is a hard wall, but `business.tiktok.com` is a real DOM.

1. Sidecar session: `/session/start {platform:"tiktok"}` — tiktok.com cookies
   already in `/app/cookies/` keep the consumer session logged in.
2. `ads.tiktok.com/i18n/login/` → **"Log in with TikTok"** SSO — NOT email+password.
   The SSO may offer `tbaltzakis` (personal) — that's the account the
   Business Center is registered under; authorize it.
3. Email OTP: code goes to `t***s@cloudless.gr` → fetch with
   `.devin/skills/mail-code-fetch/scripts/fetch-code.py tiktok tiktok 15m`
   (doveadm on omv-ha, newest-first).
4. OTP input is React-controlled — `i.value=` sets do NOT register. Type
   via RFB trusted input: `rfb-input` skill → `rfb.py click <input>` to
   focus, then `rfb.py type CODE`. DOM→screen ≈ +80px for browser chrome.
5. Land in Business Center → run `resubmit-via-sidecar.py` for the
   Not approved → Edit → cert-swap → submit flow (real DOM clicks work).

## Scripts

- `scripts/login-bootstrap.py` — opens login in sidecar, waits for session
- `scripts/resubmit-via-sidecar.py` — drives the resubmit once logged in
- Companion: `mail-code-fetch` (email OTP), `rfb-input` (trusted typing)

## Doc requirements

Clean official ΓΕΜΗ/AFM certificate (business name + AFM 115307716 legible).
Push to sidecar `/session/upload` or place under `/sdcard/Download/` on phone.
Do NOT fabricate or alter the legal document — owner provides it.

## Rejection context

Verification rejected once already. Re-submit only after replacing the cert
with the clean official document, else TikTok may flag repeat submissions.
