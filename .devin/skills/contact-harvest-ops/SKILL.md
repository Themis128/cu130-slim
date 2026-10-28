---
name: contact-harvest-ops
description: >
  Turn LinkedIn exports / CSVs / mailbox dumps into a filtered, deduped
  survey audience. Parses LinkedIn connections.csv (Data privacy export),
  generic CSVs, or pasted lists; strips junk/self/system senders; dedupes
  against EspoCRM + SocialAuto recipients; emits paste-ready
  `offer_survey.py audience --email=…` flags or attaches directly.
  Use when the CRM/inbox has no usable SMB-owner contacts — the owner's
  personal network is the only source. Tool: scripts/harvest_contacts.py.
---

# Contact Harvest Ops — audience from personal exports

Burned 2026-10-27: EspoCRM (202 leads → 4 mailable, all junk), the
`leads`/`email_recipients`/`whatsapp_messages` tables, the full 90-day mail
inbox (2,053 msgs — all system mail), and Nextcloud yielded **one** real
external email (`mgalanipsych@gmail.com`). The survey's 10–15 recipient list
must come from the owner's personal network — this tool turns the
LinkedIn export into that list.

## Get the export

LinkedIn → Settings → Data privacy → **Get a copy of your data** →
Connections → `Connections.csv`. First-degree contacts have emails.

## Use

```bash
python3 scripts/harvest_contacts.py Connections.csv          # classify + flags
python3 scripts/harvest_contacts.py Connections.csv \
    --attach 9f98e2ce-… --yes                                  # queue direct
```

Output: `MAILABLE` list (real, non-junk, non-owner addresses), `FILTERED`
with reasons (system senders, self, suppressed), and paste-ready
`--email=` flags for `offer_survey.py audience`.

## Rules

- Junk = `noreply`/`notification`/`mail.`/`@*linkedin*`/self addresses.
- Owner addresses are hard-filtered (never mail yourself a survey).
- `--attach` dedupes against `email_recipients` + `leads` so re-runs are
  idempotent.

## LinkedIn API path — `li_member_snapshot.py` (replaces the CSV click)

`GET /rest/memberSnapshotData?q=criteria&domain=CONNECTIONS` returns
1st-degree connections via API — **EEA members only** (Greece qualifies).

- Requires `Linkedin-Version: 202312` exactly — every other version → 426
- Requires scope `r_dma_portability_member` (or `_3rd_party`) — grant the
  app the "Member Data Portability API (Member)" product in the dev
  portal (instant approval), then re-authorize so the token picks it up
- Live-verified 2026-10-28: `403 ACCESS_DENIED` until the scope is granted

## WhatsApp verify — 2388091 decoded

`request_code`/`verify_code` are **migration** endpoints — subcode
2388091 = "number isn't being migrated". For standard Cloud API numbers:
`POST /{phone-number-id}/register` after onboarding in WhatsApp Manager.
The 136024/2388091 was an API-misuse error, not a rate limit.
