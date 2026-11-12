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
- **Wrong email ⇒ "Δεν έγινε ταυτοποίηση!"** — only the complaint's
  registered email works; any other address fails identification.
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
- **OTP gate (2026-11-12):** cold sessions get a 6-digit emailed code after
  Ταυτοποίηση. The script auto-fetches it from Gmail (refresh-token chain,
  `gmail-token.json` + `gcp-oauth.keys.json` under `~/.config/devin/secrets`,
  `%APPDATA%/devin/secrets`, or `/mnt/c/Users/*/AppData/Roaming/devin/secrets`;
  override dir via `SYNIGOROS_GMAIL_SECRETS`) and submits via native
  `/session/fill` + a real `/session/click` — synthetic Enter does NOT submit
  the form. Up to 3 attempts, ~20s apart; each retry needs a newer email.
- Identify works on `browser-novnc-adhoc` (port 9224 / host 9232); the main
  bridge 9223 is usually held by engagement/skool sweeps → 409s.
