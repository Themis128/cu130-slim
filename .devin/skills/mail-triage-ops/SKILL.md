# mail-triage-ops

Sweep `tbaltzakis@cloudless.gr` (omv-ha dovecot IMAP) for phishing, spam,
and actionable issues — classify, purge, report.

## Requires

`MAILBOX_PASSWORD` env (user provides — never store in repo/env files).

## Tool

```bash
MAILBOX_PASSWORD='…' python3 .devin/skills/mail-triage-ops/scripts/mail_triage.py scan
MAILBOX_PASSWORD='…' python3 .devin/skills/mail-triage-ops/scripts/mail_triage.py purge
MAILBOX_PASSWORD='…' python3 .devin/skills/mail-triage-ops/scripts/mail_triage.py subjects 20
```

Buckets: `suspect` (phishing — purged by `purge`), `ops` (OMV/disk/cron
noise), `signal` (publish/report/connected — real platform events),
`review` (needs human eyes).

## Closing the loop on misses

When a suspect lands unflagged (`X-Spam-Flag` absent):

1. Patch `SPAM_SUBJECT` regex in
   `cloudless.gr/workers/mail-ingest/src/index.ts` (add the term/language).
2. Add the sender domain to `SPAM_BLOCK_SENDERS` in `wrangler.jsonc`.
3. **Deploy with the explicit config flag:**
   `wrangler deploy --config wrangler.jsonc` — a bare `wrangler deploy` in
   `workers/mail-ingest/` resolves the PARENT `workers/wrangler.json` and
   redeploys `cloudless-analytics` instead (happened 2026-10-29 — harmless
   re-deploy of its own code, but the mail-ingest change silently no-ops).
4. The Greek-phishing terms + `speed.at` blocklist shipped 2026-10-29.

## Decoding gotcha

Subject headers can be malformed base64 — decode with a try/except wrapper
(`make_header` throws UnicodeDecodeError mid-string). `mail_triage.py`
already does this; copy the `dec()` helper, not a bare decode.
