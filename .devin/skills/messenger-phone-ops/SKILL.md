# Messenger Phone Ops

Operate the Meta Messenger surface on the owner's Galaxy S22+ (SM-S906B,
tailnet `100.84.31.85`) — verify which lane each app serves, check the
business inbox, switch Messenger profiles, and triage the cosmetic
"Waiting for network" banner.

## When to use

- "Where are my business/personal Messenger messages" on the phone.
- Verify bot auto-replies are landing on-device (Page or personal lane).
- The Messenger app shows "Waiting for network" or looks logged out.
- Adding/confirming the cloudless.gr professional surface on the phone.

## Canonical app split (verified 2026-11-28)

| App | Package | Lane |
|-----|---------|------|
| Meta Business Suite | `com.facebook.pages.app` | **Business inbox** for the cloudless.gr Page — Inbox tab, labels (Qualified/Important), away status, saved replies. This is the management surface. |
| Messenger | `com.facebook.orca` | **Personal lane** (Themistoklis Baltzakis). The cloudless.gr Page profile also exists under Menu → Switch profile for basic thread viewing only. |

The owner should read **personal** messages in Messenger and **business**
messages in Business Suite. Both profiles are already signed in inside
Messenger — switch via Menu (bottom-right) → "Switch profile" → profile →
Continue. Default posture: personal profile active, Page signed in.

## Pre-flight

Per `phone-ui-automation` golden rules: ADB must be live and the flip
cover open — `CoverHomeActivity` foreground is a hard blocker. If ADB is
down: `adb connect 100.84.31.85:5555`; if wireless debugging is OFF it's a
human gate (Termux SSH :8022 cannot re-arm it — no WRITE_SECURE_SETTINGS).

## Tools

```bash
# One-shot state classification (app presence, foreground, banner, profile hints)
.devin/skills/messenger-phone-ops/scripts/messenger_phone_check.sh [--dump]

# Deeper UI work — use the toolkit
python3 ~/galaxy-s22-toolkit/phone_ui.py status|dump|tap|text|launch
```

## "Waiting for network" banner — cosmetic class

A persistent banner with a working inbox = stale realtime-socket state,
not connectivity. Verified non-causes: Tailscale VPN, mobile-data pinning
(rmnet0), clock skew, stale app version, app cache. **Fix path**: Menu →
Switch profile → switch once (socket re-binds) — clears the banner without
a reboot. A device restart also clears it but kills wireless debugging.

**Keep `com.facebook.orca` excluded from the phone's Tailscale tunnel**
(split-tunnel: Tailscale → ⋮ → App split tunneling → Messenger checked).
Meta sockets are fragile under VPN MTU-1280 (phone-net-ops).

## On-device → code-lane verification

Observed strings map to code lanes (full map:
`docs/messenger-architecture.md` → "Phone Client Surface"):

- `🤖 Auto-reply: …` → `messenger_chatbot.py` `disclosure_prefix`
  (first-reply Meta disclosure)
- Greek pricing reply → `messenger_chatbot.py` `_PRICING_RESPONSES["greek"]`
  deterministic guardrail
- "I'll get back to you soon" vs "We'll get back to you soon" → worker vs
  `messenger-sidecar` fallback texts

## Related skills

- `messenger-ops` — the lane architecture, webhooks, bot config
- `phone-net-ops` — Tailscale/PMTU/doze triage for phone connectivity
- `phone-ui-automation` — ADB/uiautomator golden rules and pre-flight
