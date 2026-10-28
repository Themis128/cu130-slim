# 23 — Poll a Synigoros (Ombudsman) complaint's status

Checks the Greek Ombudsman complaint-tracking page and returns the live
status, protocol number, and tracking code — via the shared adhoc browser
bridge, no manual login needed after the first identification.

## Prerequisites

- `SYNIGOROS_MAIL` + `SYNIGOROS_KPA` set in the environment (or pass
  `--mail`/`--kpa` explicitly). ΚΠΑ is the tracking code from the
  submission receipt — distinct from the Αριθμός Πρωτοκόλλου (protocol #).
- The `browser-novnc-adhoc` container up (the script runs from inside
  `social-api` or anywhere the bridge is reachable).

## Check the complaint

```bash
docker exec -i social-api python \
  .devin/skills/synigoros-ops/scripts/synigoros_status.py
```

Output: `{"status": "Αναμονή", "proto": "...", "kpa": "..."}` on success.

## Check a different complaint

```bash
synigoros_status.py --mail someone@example.com --kpa 123456789
```

The script verifies the page's displayed ΚΠΑ against the requested one —
a cached session showing another complaint fails with `WRONG-COMPLAINT`
rather than returning the wrong status.

## Failure states

| Output | Meaning | Action |
|---|---|---|
| `IDENTIFY FAILED` | Wrong mail/ΚΠΑ pair | Recheck the receipt |
| `INCOMPLETE` | Challenge page or slow transition | Retry in a few minutes |
| `WRONG-COMPLAINT` | Cached session shows another complaint | Reset bridge session / pass explicit creds |
| `bridge 409` | Another platform holds the browser | Wait ~5 min and retry |
| `tracking page never rendered` | Site down or markup changed | Check synigoros.gr manually |

## Alert on status change

Add `--notify` to post to the Cloudless Telegram channel when the status
differs from the last recorded run (state in `/tmp/synigoros_last.json`).
