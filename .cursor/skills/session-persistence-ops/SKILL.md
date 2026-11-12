---
name: session-persistence-ops
description: >-
  Keep social platform sessions alive programmatically — the researched
  patterns for avoiding re-auth entirely: full session-blob persistence
  (instagrapi dump_settings), storageState/cookie transplant instead of
  re-login, session warm-keepalive pings, checkpoint detection with
  headed/CDP fallback, and credential-vault extraction. Use when sessions
  keep dying, checkpoints keep appearing, or deciding how to automate a
  remaining human login gate.
---

# Session Persistence Ops

Research-backed patterns (2026-11-12) for the stack's remaining human
gates — LinkedIn/Facebook/Instagram sessions that die to Cloudflare
challenges, checkpoint redirects, and cookie rejection. The unifying
finding across every source: **re-login is the failure mode — persist and
resume the session instead.**

## Pattern 1 — Full session blob > bare cookie (instagrapi)

Bare `sessionid` login on a fresh device fingerprint is what triggers IG
checkpoints. instagrapi's `dump_settings()`/`load_settings()` serialises
the device fingerprint + cookies + headers + user pk; loading it makes
each login look like the same phone resuming — challenge rate drops ~10x
per instagrapi's own persistence guide.

**Stack surface:** `InstagramPrivateDMClient` accepts
`settings_blob=` → `private_api_session_settings` in
`social_accounts.meta_data` (encrypted via `encrypt_field`). Capture /
verify / clear with:

```bash
docker compose exec -T social-api python3 scripts/ig_session_blob.py \
  --account <uuid> --sessionid <sid>     # capture blob into meta_data
docker compose exec -T social-api python3 scripts/ig_session_blob.py \
  --account <uuid> --verify              # is the stored session alive?
docker compose exec -T social-api python3 scripts/ig_session_blob.py \
  --account <uuid> --clear               # fall back to bare sessionid
```

Same idea for TikTok private API (`tiktok-private-api`) — its session
objects also carry device state worth persisting.

## Pattern 2 — storageState / cookie transplant, never re-login

Playwright `storageState` (cookies + localStorage + IndexedDB) or CDP
`Storage.getCookies` from a trusted browser context injected into the
target lane preserves the TLS/JA3 fingerprint of a REAL session — the
thing cookie-paste tools (Romek-style vaults) can't replicate with plain
HTTP clients. LinkedIn `li_at` is fingerprint-bound: transplanting it
into a different fingerprint gets it revoked globally — only transplant
into the same browser engine/profile class.

**Stack surface:** `session-ops` transplant section; bridge
`browser_storage_state` in `meta_data` (FB heal path); LinkedIn stays on
its own sidecar profile — do NOT move `li_at` to the bridge.

## Pattern 3 — Session warm-keepalive

Cookie sessions decay server-side with inactivity; a cheap authenticated
GET every ~30–60 min keeps them warm AND detects death early (better
than discovering it at publish time). Combined with `fb_dtsg`-style
token refresh cadences (~20 min for FB private lanes).

**Stack surface:** `session_healer.heal_all_sessions()` hourly :20 beat
already probes every lane — it detects but doesn't warm. A dedicated
warm ping per sidecar (`GET /session/validate` or a feed/profile fetch)
is the incremental add if death-by-inactivity becomes a pattern.

## Pattern 4 — Checkpoint classification → graduated response

Detect the wall type before choosing the remedy:

| Signal | Meaning | Response |
|---|---|---|
| `/checkpoint/`, `/challenge/`, captcha iframe | Soft checkpoint | Fresh tab + retry once; then escalate |
| Login wall / `authwall` | Session dead | Credential login lane (sidecar `/login`) |
| Cloudflare "Just a moment" | IP/fingerprint flag | Slow down; headed lane; residential-IP note |
| `li_at` revoked / `login_required` | Token dead | Re-auth lane (OAuth or credential) |

Never hammer a checkpoint — repeated headless attempts escalate to
temporary restrictions (linkedin-buddy documents this). Cool down ≥ the
healer's backoff.

**Stack surface:** each healer already classifies; `human-gate-ops` /
`manual-gates-ops` own the escalation to the :9229 CDP lane + noVNC
:6080 for the human click.

## Pattern 5 — Headed first-login, headless steady-state

The proven lifecycle (linkedin-browser-mcp, browser-agent): ONE visible
login in a persistent on-disk profile → reuse headless forever. Our
`:9229` debug Chrome + sidecar profiles already implement this — the gap
is only when the profile itself is flagged, which needs the human gate.

## What is NOT automatable (correctly human)

- First-ever login / credential entry with 2FA app approval
- Cloudflare interactive challenges on a flagged IP+fingerprint
- Platform appeals and identity verification

Everything else — detection, transplant, warm-ping, blob persistence,
cooldown — is covered above or in `session-ops`.
