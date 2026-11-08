# gsis-login-ops

Automated login for portals federated through Greek GSIS/TaxisNet OAuth
(`oauth2.gsis.gr`), including the second-step `LoginAMKA` page some portals
(e.g. IDIKA) show after GSIS certification. Drives the `chrome-debug-profile`
CDP lane (`:9229`) — the profile's cookies are the session; keep the browser
running.

## Flow (as implemented in `scripts/gsis_login.py`)

1. Open the portal's login page, click the "Συνέχεια στο TaxisNet" submit
   (`#Gsis.OAuth2` on IDIKA; parameterize per portal).
2. `oauth2.gsis.gr/oauth2server/login.jsp`: fill `#j_username` (AFM) +
   `#j_password`, click `#btn-login-submit` — **only if the form appears**;
   a live GSIS session skips straight to `/oauth/authorize`.
3. Consent page: click `#btn-submit` ("Αποστολή") / any "Συνέχεια|continue|
   αποδοχή|approve|αποστολή" button (excluding "είσοδος|επιστροφή").
4. Optional `*/Account/LoginAMKA` step: fill `#AMKA`, submit "Είσοδος".
5. Verify by re-checking the portal's auth marker (DOM, not URL).

## Credentials

`%APPDATA%/devin/secrets/taxisnet.json` (host-local, never committed):
`{"username":"<AFM>","password":"<pw>","amka":"<AMKA>"}`

GSIS oAuth2.0 (non-OTP, e.g. IDIKA client `LK4B7J43558`) uses TAXISnet
credentials only — no inherent 2FA. A challenge is risk-based; on failure
alert once and let the user log in manually in the debug window.

## Usage

```
python scripts/gsis_login.py --portal idika          # ensure IDIKA session
python scripts/gsis_login.py --portal idika --check  # report only
```

Also importable: `ensure_login(portal, notify=None) -> bool`.

## Notes

- All auth cookies are browser-session cookies — closing the debug Chrome
  forces full re-auth.
- The generic engine in `gov-portal-watch/scripts/engine.py` embeds the same
  flow (`gsis_login()`); this skill documents it standalone.
