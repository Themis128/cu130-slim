---
name: viber-bot-ops
description: Viber bot provisioning — BLOCKED at bot creation (commercial-only since 2024-02). partners.viber.com RETIRED, and the phone account has NO bot (verified 2026-11-12 — chat/business search for 'cloudless' returns nothing). Token capture path is automated for when a bot exists (viber_phone_capture.py), but the gate is a Rakuten Viber commercial application, not a credential lookup. viber_token.py --check reports connection state.
---

# Viber bot provisioning

**Current blocker (verified live 2026-11-12):** there is no bot on this
Viber account and self-serve bot creation is closed. New Viber bots are
commercial-only — apply via Rakuten Viber / an official partner
(cecile.tizon@viber.com per respond.io docs). Legacy bots (pre-2024-02)
still show the token in-app; this account has none.

## Once a bot exists — fully automated capture

`viber_phone_capture.py` drives the phone over ADB:
launch Viber → open the bot chat → Edit Info → App Token → copy →
clipboard via Termux SSH (`termux-clipboard-get`) → validate →
`viber_provision.py --token <t> --name cloudless` (connect → validate →
webhook set — all unattended).

- `--probe` reports ADB devices + current screen; `--device` pins a
  serial (the phone is reachable as both a LAN and tailnet ADB address).
- `--no-provision` captures the token without wiring it.
- If navigation stalls it prints the on-screen texts — Viber UI labels
  differ per version, extend the label lists rather than tapping blind.

`adb_sms.py` reads phone-SMS codes via ADB for any OTP step. The portal
drive in `viber_token.py` is kept for the token-capture path if a new
web surface appears.
