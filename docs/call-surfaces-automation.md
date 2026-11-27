# Call-Surface Automation Layer

**Created:** 2026-11-27 — codifies the `cloudless.gr/call` rollout + the
tooling that keeps every public surface consistent.

## What it covers

13 public surfaces carry the `cloudless.gr/call` CTA: website pages, FB page
+ personal bio, Threads (`t_baltzakis`), Instagram (`t_baltzakis` +
`cloudless.gr`), Bluesky, Telegram bot + channel, X (`@TBaltzakis`),
LinkedIn org, TikTok, WhatsApp Business profile.

The tooling lives in user-level skills (Windows host):
`%APPDATA%\devin\skills\phone-profile-ops\` and
`%APPDATA%\devin\skills\whatsapp-business-ops\`.

## Components

| Component | Where | What |
|---|---|---|
| `call_audit.py` | `phone-profile-ops/scripts/` | One-command drift audit, 3 lanes |
| `profile_ops.py` | `phone-profile-ops/scripts/` | uiautomator2 bio setters per app |
| `adb_ui.py` | `phone-profile-ops/scripts/` | raw-ADB UI primitives (fallback) |
| `whatsapp_profile.py` | `whatsapp-business-ops/scripts/` | WhatsApp business_profile get/set inside social-api |
| `CallSurfacesAudit` | Windows schtasks | Daily 09:15, `pythonw`, `--lane public,api --notify` |

## call_audit.py — three lanes

```bash
python call_audit.py                  # all lanes
python call_audit.py --lane public,api
python call_audit.py --notify         # FAILs → Telegram ops channel
```

- **public** — site `/call` + `/en/consultation` 200s; Bluesky profile via
  `public.api.bsky.app`; X bio via `api.fxtwitter.com` (the old syndication
  `followbutton` endpoint is dead — do not resurrect it).
- **api** — `docker exec -i social-api python -` snippet: FB page via the
  `/accounts` *page* token (user token can't read `about`/`description`),
  WhatsApp `business_profile` on phone_number_id `1432734783254091`,
  LinkedIn org `108614163` with `Linkedin-Version: 202610` header
  (202511 → 426 NONEXISTENT_VERSION), Telegram `getMyDescription` +
  `getChat -1004436942504`, and **BM `1558125105019725`
  `verification_status`** — PASS on `pending`/`verified`, FAIL → alert on
  `pending_need_more_info`/`rejected`.
- **phone** — uiautomator2 dumps of FB personal profile, IG ×2, Threads,
  TikTok. On-demand only: a locked phone would false-alarm in the
  scheduled run.

## profile_ops.py — bio setters

```bash
python profile_ops.py tiktok --bio "Audit + call: cloudless.gr/call"
python profile_ops.py tiktok --check
```

Apps: `tiktok`, `instagram`, `threads`, `x`, `telegram_channel`,
`facebook_personal`. uiautomator2 3.7.1; `d.set_text`/`send_keys(clear=True)`
replaces keyevent-clearing; `watch_context(builtin=True)` eats popups
mid-flow; `d.unlock()` wakes + swipes.

**PIN unlock is scripted**: `PHONE_UNLOCK_PIN` lives in the SocialAuto
secret store (postgres + D1 mirror); `profile_ops.unlock_pin(d)` =
`d.unlock()` → keyguard check via `dumpsys` → `input text <pin>` + ENTER.
Verified live on a locked/dozing Galaxy S22+.

## Meta OAuth — fully unattended

`meta_oauth_drive.py` (`.cursor/skills/meta-oauth-ops/scripts/`) self-serves
the 2FA gate: `GATE_2FA` generates a dependency-free inline TOTP
(RFC-6238 HMAC-SHA1/30s/6-digit) from `META_TOTP_SECRET` /
`META_TOTP_SECRET_AC` / `META_TOTP_SECRET_IG` (container env), clicks
"Try another way" → "Authentication app" → fills `approvals_code` →
submits. Falls back to human-wait when no secret is set.

Chain: mint `/api/v1/auth/oauth/{platform}/authorize` URL → login fill →
TOTP 2FA → consent click → callback → token verify — zero human clicks.

## Business verification watcher

BM `1558125105019725` verification submitted 2026-11-27 ("In review").
The daily audit's `bm_verification` check polls `verification_status` —
regression to `pending_need_more_info`/`rejected` fires a Telegram alert.
On `verified`, the 10:00 `meta-infra-retry` beat lands the system-user
mint + WABA/ad-account chain automatically (clears the #100/2859015 gate).

## Gotchas worth remembering

- Social UI text is NOT `%`-decoded — type `&`, `%`, spaces literally.
- TikTok bio cap is 80 chars — "Audit + call: cloudless.gr/call" fits.
- `social_accounts` column is `account_id`, not `external_account_id`;
  team_id `10024d25-2ea4-40ea-86c6-ba89f515a2eb`.
- Meta doc-upload auto-check does literal text matching — legal
  name/address must be entered in **Greek** to match Greek documents.
- Meta OAuth consent clicks are scripted DOM clicks (platform requires
  the flow, not the human) — but document upload was the one genuinely
  physical artifact (accepted doc showing legal name + address).
