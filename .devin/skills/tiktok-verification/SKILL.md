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
# tiktok-verification — field notes (2026-10-29)

## Rejection root cause (two submissions, same verdict)

Business Suite → Verify → Business registration shows **"Not approved"**
twice. The form field is **"Official General Commercial Registry
Certificate: 115307716"** — 115307716 is the owner's **ΑΦΜ (tax number)**,
NOT a ΓΕΜΗ registry number. Greek sole traders have no ΓΕΜΗ entry, so the
"registry certificate" doc type can never match — review rejects on
doc-type mismatch regardless of image quality.

## Fix paths (in order)

1. **Doc-type selector** — the certification step has a type dropdown;
   pick "tax registration"/"sole trader" if offered and re-upload the
   ΑΑΔΕ ενημερότητα (`/sdcard/Download/cert.png`, ΑΦΜ matches name field).
2. **Business type in basic info** — if the form allows "individual/sole
   trader" instead of company, the ΑΑΔΕ cert becomes valid input.
3. **Real ΓΕΜΗ extract** — only if an ΙΚΕ/company entity exists; free PDF
   from businessportal.gr publicity search (needs recaptcha — do it in a
   browser, not ADB).

## Verified coordinates (1080x2340, guard with screenshots)

- Profile tab: (957,2148) → Business Suite: (585,729) → Verify: (777,900)
- "Got it" modal: (540,1342) → Edit: (184,544) → dialog Edit: (337,1307)
- Next: (540,2057) → upload tile: (480,778) → Submit: (540,2070)
- Picker: re-push cert to make it newest → tile-1 circle (222,237) → OK (1) (1020,128)
- ntfy steals focus — `pm suspend io.heckel.ntfy` mid-run, restore after.

## Status

Submitted 2026-10-29 ~20:00 → rejected ~22:00 same day (review is FAST,
not 3 business days). Next attempt must fix the doc TYPE, not the file.

# tiktok-verification — web Business Suite path (2026-11-12, WORKING)

The `business.tiktok.com` / `business-suite` web flow is fully drivable —
no CAPTCHA on the consumer session. `tt_verify_resubmit.py` now does the
whole thing unattended:

1. Navigate `business-suite/business-registration/verify?source=onboarding`
2. Policy page → checkbox → Get started → verify form
3. Click Upload → inject cert via `DataTransfer` → `input.files` → change
   event (b64 over stdin through `docker exec -i … node -e` — argv limit)
4. Click the LAST "Edit" button → expands the editable form + Submit
5. Set company name to the GREEK legal name + AFM → Submit

## Root cause of the "Company name inconsistency" rejection

`cert.pdf`/`cert.png` in Downloads is the **AADE tax certificate**
(ΑΑΔΕ — «ΑΠΟΔΕΙΚΤΙΚΟ ΕΝΗΜΕΡΩΤΗΚΟ ΓΙΑ ΧΡΕΗ ΠΡΟΣ ΤΟ ΔΗΜΟΣΙΟ», ΑΦΜ 115307716,
name printed as **ΜΠΑΛΤΖΑΚΗΣ ΘΕΜΙΣΤΟΚΛΗΣ**). The form had the Latin
"Baltzakis Themistoklis" — script mismatch, not a doc-type problem
(the earlier ΓΕΜΗ theory was wrong; this doc type is accepted).

Name must be entered in Greek: `Μπαλτζάκης Θεμιστοκλής`.

## OCR the cert (no tesseract on WSL)

Windows has `el`+`en-US` OCR built in — `powershell.exe` +
`Windows.Media.Ocr.OcrEngine` (see `C:\Users\tbaltzakis\ocr.ps1`,
one-liner: `powershell -File ocr.ps1 -Path <winpath> -Lang el`).
PDF text layer is empty (vector-outlined scan); OCR the PNG instead.

## Status watcher

`tt_verify_status.py` — navigates to the verify page, parses
Under Review / Approved / Disapproved+reason, alerts #ops on change.
State: `~/.local/state/tiktok-verify/status`. Cron: every 6h.

## Submitted 2026-11-12

cert.png attached + name `Μπαλτζάκης Θεμιστοκλής` + AFM 115307716 →
**"Under Review" / "Submitted successfully!"**. Watcher armed.
Note: cert address (Οδός Ιωάννη 15, 19400) differs from the form's
business address (Thermopilon 15, Koropi 19441) — if TikTok rejects on
address next, that's the next field to reconcile (fiscal vs operating).
