---
name: totp-ops
description: >
  Close the X 2FA human gate — drive the authenticator-app enrollment on
  the debug Chrome (consent-root-ops CDP :9222), capture the otpauth
  secret into .env TWITTER_TOTP_SECRET, and wire pyotp into the
  consent_healers credentials-login fallback so every X re-auth is
  unattended. Tool: scripts/totp_setup.py — --check reports CDP+secret
  state, --code prints a live code, no-args drives the enrollment.
  Requires: chrome-debug-launch.bat bound once at a Windows desktop
  session (ChromeDebug scheduled task set as logon fallback).
---

# TOTP Ops

The last X gate. `totp_setup.py --check` → bound? → drive enrollment →
secret → `pyotp` in `_x_credentials_login` → 100% unattended re-auth.
