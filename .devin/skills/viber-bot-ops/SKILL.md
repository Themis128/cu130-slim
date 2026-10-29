---
name: viber-bot-ops
description: Provision the Viber bot — drive the partners.viber.com portal on
  the adhoc session to reach the bot list + App Token, then auto-provision
  via viber_provision.py (connect → validate → webhook). The one human gate
  is the Viber phone-OTP login on the portal (partners.viber.com needs the
  account phone). Tool: scripts/viber_token.py.
---

# Viber bot provisioning

`viber_token.py` → partners.viber.com on the adhoc session → bot list → App
Token → `viber_provision.py --token` (connect/validate/webhook). The portal
login needs the account phone's OTP — the only irreducible step.
