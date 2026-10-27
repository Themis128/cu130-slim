---
name: audience-watch-ops
description: >
  Watch a directory (Downloads) for contact exports and auto-attach them to
  the survey campaign — the user drops `Connections.csv` (LinkedIn data
  export) or any CSV/TXT with emails, the watcher harvests, filters
  junk/self, dedupes, and attaches via `offer_survey.py audience`. The
  `survey_autosend` beat then sends within 15 min — fully hands-free from
  file-drop to delivered. Tool: scripts/audience_watch.py. Companion to
  `contact-harvest-ops` (the classification rules) and `offer-survey-ops`.
---

# Audience Watch Ops — drop a file, the survey sends itself

The last manual input on the survey pipeline is the recipient list. This
skill removes it: the watcher polls the Downloads dir for contact exports
and attaches them automatically.

## Use

```bash
python3 scripts/audience_watch.py              # one-shot sweep
python3 scripts/audience_watch.py --daemon     # poll every 30s
python3 scripts/audience_watch.py --campaign <CID>
```

Watches `/mnt/c/Users/tbaltzakis/Downloads` + `~/Downloads` for
`*Connections*.csv` / `survey*.txt` / any `.csv`/`.txt` with emails.

## Chain

```
drop Connections.csv → harvest → filter junk/self → attach via
offer_survey.py audience → meta_data.auto_send survey_autosend beat →
delivered (15 min) → responses <CID>
```

## Three remaining human inputs (each now a wrapper)

| Input | Surface |
|---|---|
| Contact export | this watcher — drop the file, done |
| X re-auth | `x-oauth-wire/scripts/wire.py <client_id> <secret>` |
| WhatsApp verify | `whatsapp_tool.py request_code` → SMS → `verify_code <code>` |
