---
name: pending-inputs-ops
description: >
  One command for every remaining human input — status dashboard, fresh
  OAuth re-auth URLs, and unattended WhatsApp verify retry. Use to answer
  "what's left for the user?" or to hand someone the exact click-links.
  Tool: scripts/.
---

# Pending-Inputs Ops — all remaining inputs, one surface

## Commands

```bash
pending_status.py      # dashboard: twitter/linkedin/whatsapp/csv/recipients
reauth_urls.py         # fresh OAuth authorize URLs for twitter + linkedin org
wa_verify_daemon.py    # retries request_code hourly → dual-polls → verifies
```

## What each input needs (as of 2026-10-28)

- **twitter (expired)** — the OAuth approve click; `reauth_urls.py` prints
  a fresh URL, callback lands server-side automatically
- **linkedin org** — token predates the Community Management approval →
  `426 NONEXISTENT_VERSION` until re-authorized; same click pattern
- **WhatsApp phone** — Meta `136024/2388091` rate-limited; the daemon
  retries `request_code` hourly for up to 3 days then hands to
  `wa_verify.py` (ADB+mail dual-poll) — fully unattended
- **Connections.csv** — `audience_watch --daemon` attaches on drop;
  `li_export_watch.py --daemon` catches the export-ready email

Run `pending_status.py` anytime for the live picture.
