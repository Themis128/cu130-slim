---
name: dev-app-audit
description: >
  One-shot consolidated audit of every connected social developer app —
  runs the per-platform *_tool.py CLIs (meta/linkedin/x/tiktok/whatsapp/
  instagram/viber/bluesky) and normalizes a status table flagging expired
  bearers, revoked dupes, unverified phones, and inconclusive probes.
  Use when asked to "check all developer applications", after the 6h
  reconcile sweep flags drift, or before assuming a channel is healthy.
  Tool: scripts/app_audit.py. Human steps the tool can't do: X OAuth2
  re-auth, WhatsApp phone verification in Meta Business.
---

# Dev App Audit — every developer app, one table

Wraps `social-automation/backend/scripts/*_tool.py` (meta `accounts`,
linkedin `accounts`, x `validate`, tiktok `validate`, whatsapp `status`,
instagram/bluesky/viber) inside `social-api`, normalizes each app's
live state, and exits nonzero on any expired/revoked/unverified signal.

```bash
python3 scripts/app_audit.py            # table + exit code
python3 scripts/app_audit.py --json     # machine-readable
```

## Known states (2026-10-27 baseline)

- X/Twitter bearer → 401; OAuth1 may still post but needs re-auth in-app
  (session-healer covers OAuth1 only)
- WhatsApp `code_verification_status: NOT_VERIFIED` — submit phone verify in
  Meta Business Manager to unblock WABA sends
- One stale revoked FB page row sits beside the working `cloudless.gr` page —
  the reconcile sweep keeps it revoked; delete it manually if it annoys
- Meta/IG/Threads/LinkedIn tokens all expire ~Dec 2026 — no action needed
