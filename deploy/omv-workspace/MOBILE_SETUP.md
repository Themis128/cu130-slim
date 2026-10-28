# Cloudless Workspace — Mobile Setup (Android & iOS)

Server: `https://cloud.cloudless.gr` · Account: `tbaltzakis@cloudless.gr`

**Prerequisite (do once, on any device):** add the TOTP secret to an
authenticator app — the secret is in `RECOVERY_SECRETS.txt` on the omv SSD
(`workspace/RECOVERY_SECRETS.txt`) — never committed to git.

---

## Android

### 1. Authenticator app (2FA)
- Install **Aegis** or **2FAS** (both free, F-Droid/Play Store).
- Add account → enter key manually → paste the TOTP secret → name it
  "Cloudless".

### 2. Nextcloud app (files + photos + docs)
- Install **Nextcloud** (Play Store / F-Droid).
- Sign in → server `https://cloud.cloudless.gr` →
  `tbaltzakis@cloudless.gr` + password → TOTP code from authenticator.
- In app settings → **Auto upload** → enable for Camera folder
  (replaces Google Photos backup).

### 3. Calendar & Contacts sync — DAVx5
- Install **DAVx⁵** (free on F-Droid, paid on Play Store).
- Add account → "Login with URL and user name" →
  URL `https://cloud.cloudless.gr` → credentials.
- DAVx5 uses Nextcloud's Login Flow — it opens a browser sheet, handles
  2FA automatically, and provisions an app password itself.
- Select the Calendar + Contacts collections → syncs into native
  Android Calendar/Contacts apps.

### 4. Nextcloud Talk (calls/chat)
- Install **Nextcloud Talk** (Play Store / F-Droid).
- Add account → `https://cloud.cloudless.gr` → same login flow.
- Grant mic/camera permissions for calls.

### 5. Office documents
- Documents open inside the Nextcloud app via Collabora automatically —
  no separate app needed.

---

## iOS / iPadOS

### 1. Authenticator (2FA)
- **Apple Passwords** (iOS 18+) supports TOTP natively, or install
  **2FAS** / **Raivo OTP** (free).
- Add entry → manual key → paste TOTP secret → name it "Cloudless".

### 2. Nextcloud app
- Install **Nextcloud** (App Store).
- Sign in → `https://cloud.cloudless.gr` → credentials + TOTP code.
- The account also appears in the iOS **Files** app automatically
  (browse/upload without opening Nextcloud).
- Settings → **Auto upload** for photos (replaces iCloud Photos backup).

### 3. Calendar (CalDAV) — needs an app password
Because 2FA is enforced, iOS native sync can't use your main password:

1. In a browser: `cloud.cloudless.gr` → profile → **Settings →
   Security → Devices & sessions → Create new app password** → name it
   "iOS Calendar" → copy the generated password.
2. iPhone: **Settings → Apps → Calendar → Calendar Accounts →
   Add Account → Other → Add CalDAV Account**.
   - Server: `cloud.cloudless.gr`
   - User: `tbaltzakis@cloudless.gr` · Password: the app password
   - Description: Cloudless
3. If it needs the full path: `Advanced Settings` →
   `https://cloud.cloudless.gr/remote.php/dav/principals/users/tbaltzakis@cloudless.gr/`
   (use SSL, port 443).

### 4. Contacts (CardDAV) — same app-password pattern
- Settings → Apps → Contacts → Contacts Accounts → Add Account →
  Other → **Add CardDAV Account** → same server/credentials
  (generate a second app password "iOS Contacts", or reuse one).

### 5. Nextcloud Talk
- Install **Nextcloud Talk** (App Store) → server
  `https://cloud.cloudless.gr` → login flow handles 2FA.
- Allow mic/camera/notifications when prompted.

---

## Notes

- **App passwords**: any client that can't do the web login flow
  (CalDAV/CardDAV, WebDAV mounts, third-party apps) needs an app
  password from Settings → Security — your real password + TOTP only
  works in browser-aware flows.
- **Push notifications** on mobile Talk/Nextcloud work via Nextcloud's
  public push gateway — no config needed.
- **External Talk calls**: until the router forwards UDP+TCP 3478 to
  omv, calls on cellular/outside the LAN may fall back to P2P —
  fine for 1:1, may struggle in groups behind strict NATs.
- All apps are free. DAVx5 is gratis on F-Droid; the Play Store version
  is a paid donation build (functionally identical).
