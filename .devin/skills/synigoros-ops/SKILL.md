---
name: synigoros-ops
description: >
  Poll the Greek Ombudsman (Συνήγορος του Πολίτη) complaint-tracking page
  for live status — automates the email+ΚΠΑ identify on the adhoc browser
  bridge and extracts Κατάσταση / protocol. Use for "did the Synigoros
  reply", complaint status, or the monitor's daily check.
  Tool: scripts/synigoros_status.py.
---

# Synigoros Ops — complaint tracking via browser bridge

## The flow (learned 2026-10-28)

- Tracking page: `https://www.synigoros.gr/el/anafora/parakolouthisi-anaforas`
- Identify needs **email + ΚΠΑ** (tracking code) — supplied via
  `SYNIGOROS_MAIL` + `SYNIGOROS_KPA` env vars (or `--mail`/`--kpa` flags);
  no identifying values are committed to the repo.
- ΚΠΑ (tracking code) and Αριθμός Πρωτοκόλλου (protocol #) are DISTINCT
  numbers on the receipt — identify uses ΚΠΑ, the status page shows both.
- **Wrong email ⇒ "Δεν έγινε ταυτοποίηση!"** — themis.baltzakis@gmail.com
  fails; the complaint's registered email works.
- A live session skips the form (status view renders directly) — the script
  handles both states.

## Commands

```bash
synigoros_status.py                    # env mail+ΚΠΑ → {"status": "..."}
synigoros_status.py --bridge http://localhost:9232   # from host (dockerless)
synigoros_status.py --mail X --kpa Y   # different complaint
```

## Gotchas

- `session/start` needs a platform arg — any supported platform boots the
  browser; `navigate` handles the real URL.
- `context was destroyed` = page navigating mid-eval — retry.
- `No active browser session` = session expired — the script re-starts it.
- email-verification codes from `noreply@synigoros.gr` expire fast and are
  per-attempt — a fresh code beats an old one.
- Identify works on `browser-novnc-adhoc` (port 9224 / host 9232); the main
  bridge 9223 is usually held by engagement/skool sweeps → 409s.
