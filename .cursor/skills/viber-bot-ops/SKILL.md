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

## Free lane: Viber Business Account (2026-11-14)

Separate from bots — **free** for micro/small business (<10k
business-account messages/calendar month, Viber Business Accounts Terms
May-2025): business profile (name/logo/description/website/category),
public directory listing after safety review, Communities/Channels as
official business chat. NO contract needed — the €115/mo + per-message
fees apply only to commercial **chatbots** (REST Bot API).

**Phone-only creation** — Viber docs: "a business account can only be
opened on your primary device." Desktop app (verified 2026-11-14, v29300)
shows no business-creation path: More menu = Viber Plus / Viber Out /
Contact list / My Notes / screen lock / Settings / Support only.

`viber_business_setup.py` drives the phone flow over ADB (reuses
viber_phone_capture machinery):

```bash
viber_business_setup.py probe          # device + screen dump
viber_business_setup.py run --dry-run  # navigate + dump, no submits
viber_business_setup.py run            # fill name/website/description/category
```

Stops at human steps (logo image pick, document upload, final submit) and
prints exactly where. **Phone currently unreachable** — wireless ADB off
(192.168.1.77 no route, tailnet :5555 refused 2026-11-14), Termux SSH :8022
needs credentials not on this host. Run `probe` once ADB is back.

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
