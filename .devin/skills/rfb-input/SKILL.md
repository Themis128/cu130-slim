# rfb-input

Trusted X-level input injection into the engagement-browser sidecar — works
where `element.click()`, synthetic keyboard events, and CDP all fail (sites
detect `isTrusted=false`). Sends real RFB pointer/keysym events to x11vnc.

## Use

```bash
# click at SCREEN coords (map DOM coords: +~80px for browser chrome, or
# read getBoundingClientRect + window.screenY to compute)
python .devin/skills/rfb-input/scripts/rfb.py click 640 400
python .devin/skills/rfb-input/scripts/rfb.py type 123456
python .devin/skills/rfb-input/scripts/rfb.py key 0xff0d   # Return
```

## Proven

- X OAuth "I trust this app" checkbox + Authorize button (consent gated —
  button disabled until box ticked; JS click landed, RFB not needed there)
- TikTok Business Center OTP field — DOM-level fills/keys/RFB keys all
  ignored; RFB click-to-focus + type is the last resort (field focus is the
  missing step — click the input first, THEN type)

## DOM→screen mapping

Xvfb is 1280x800; browser window sits at scrY=-151 (chrome ~131px).
Screen-y ≈ DOM-y + (~80px top chrome). Screenshot via
`/session/screenshot` (GET, PNG) to verify actual positions.
