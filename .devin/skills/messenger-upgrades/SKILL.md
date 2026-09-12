---
name: messenger-upgrades
description: >-
  Master index of all Messenger and browser bridge upgrades: unified inbox,
  Instagram DMs, fast mobile reads, daemon mode, E2EE PIN handling, and
  recruiting bot mode. Use as a starting point when working on any Messenger
  or browser bridge enhancement, or when you need to find which skill covers
  a specific upgrade.
allowed-tools:
  - read
  - exec
  - grep
  - web_search
triggers:
  - user
  - model
---

# Messenger Upgrades (Master Index)

Index of all Messenger and browser bridge upgrades implemented in the
Cloudless social stack. Each upgrade has a dedicated skill with full
documentation.

## Upgrade summary

| # | Upgrade | Skill | Status | Commit |
|---|---------|------|--------|--------|
| 1 | Unified inbox API | `unified-inbox` | ✅ Live | `8d359ed3` |
| 2 | Instagram DM integration | `instagram-dm` | ✅ Live | `d1e966b9` |
| 3 | Fast mobile reads | `messenger-fast-reads` | ✅ Live | `ca2385fa` |
| 4 | Browser daemon mode | `browser-daemon-mode` | ✅ Live | `717569fa` |
| 5 | E2EE PIN handling | `messenger-e2ee` | ✅ Live | `169e28ed` |
| 6 | Recruiting bot mode | `recruiting-bot-mode` | ✅ Live | (config only) |
| 7 | LinkedIn sidecar fixes | `linkedin-sidecar-ops` | ✅ Live | `37ccde47` |

## When to use which skill

| Task | Skill |
|------|-------|
| View all DMs across platforms | `unified-inbox` |
| Send/read Instagram DMs | `instagram-dm` |
| Speed up personal Messenger reads | `messenger-fast-reads` |
| Keep browser session warm for fast sends | `browser-daemon-mode` |
| Handle E2EE PIN dialog | `messenger-e2ee` |
| Configure recruiting auto-reply | `recruiting-bot-mode` |
| Update LinkedIn profile via SocialAuto | `linkedin-sidecar-ops` |

## Architecture (with upgrades)

```
                    Meta Developer Console
                            │
                    Webhook (POST events)
                            │
                            ▼
    social-api (port 8083)
    /api/v1/messenger/webhook    /api/v1/inbox/inbox (NEW)
         │                    │
         │ (dispatch)         │ (aggregate)
         ▼                    ▼
    messenger-sidecar     ┌── Page Messenger (Graph API)
    (port 9230)           ├── Personal Messenger (fast mobile read) (NEW)
         │                ├── Instagram DMs (Messaging API) (NEW)
         │ (AI reply)     └── WhatsApp (Cloud API, placeholder)
         ▼
    CF Workers AI
    → DMR fallback
    → static text

    Browser Bridge (port 9223)
    ├── Daemon mode: warm + keepalive (NEW)
    ├── Fast reads: m.facebook.com (NEW)
    ├── E2EE PIN handling (NEW)
    └── Recruiting bot mode (NEW)

    LinkedIn Sidecar (port 9225)
    ├── Fixed: specialties, headline, About, profile scroll
    ├── Session injection from SocialAuto
    └── Lazy-load handling
```

## Test gate results

All upgrades passed the test gate:

| Check | Result |
|-------|--------|
| `pytest tests/unit -q` | ✅ 549 passed, 2 skipped |
| `ruff check` (all changed files) | ✅ All checks passed |
| `docker compose config --quiet` | ✅ Valid |
| `curl http://localhost:8083/health` | ✅ ok |
| Instagram API tests | ✅ 34 passed |

## Related skills

- `messenger-management` — Core Messenger management (Pages + personal)
- `messenger-platform` — Facebook Page Messenger via Graph API
- `browser-bridge-ops` — Generic browser bridge operations
- `social-stack-ops` — Docker Compose stack operations
