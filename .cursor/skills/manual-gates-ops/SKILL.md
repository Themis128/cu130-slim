# manual-gates-ops — automate the "needs a human" leftovers

Three recurring manual gates, all with a programmatic path. Built
2026-11-03 after the mail-triage leftover list kept surfacing the same
three items.

## 1. Dead Bluesky app password → `scripts/bluesky_app_password_renew.py`

Stored cred 401s? The PDS exposes `com.atproto.server.createAppPassword`
(the endpoint bsky.app Settings calls) — mint a fresh one with the
account's **main password** as the single human input:

```bash
docker exec -i -e PYTHONPATH=/app social-api python bluesky_app_password_renew.py \
  --handle tbaltzakis.bsky.social --main-password '<pw>'
```

Probes stored cred first (no-op if healthy), mints
`socialauto-renew`, writes via `encrypt_token` to the account row,
re-verifies createSession before commit. After this, the
`sync_bluesky_account` path (PR #1288) starts writing snapshots.

## 2. Visitor chat lead → `scripts/talk_lead_reply.py`

"[Team] New Chat Conversation" mails = Talk guest rooms. The lead has no
email — reply *inside the room* via the spreed OCS API:

```bash
python talk_lead_reply.py --rooms                 # discover room tokens
python talk_lead_reply.py --needle "free audit"   # find + reply (default CTA)
python talk_lead_reply.py --room-token X --reply "…"
```

Needs `NEXTCLOUD_BASE_URL` + `NEXTCLOUD_USER` + `NEXTCLOUD_PASSWORD`
basic auth. Matches rooms by scanning last messages for the needle —
confirm the room before replying (`--rooms` first).

## 3. WSL vsock recycle → `scripts/wsl_recycle.py`

`wsl --shutdown` fixes the vsock decay but kills this session — must
fire host-side:

- Interop alive → registers a one-shot `wsl-recycle` Scheduled Task
  (default 04:00 local).
- Interop dead → drops `wsl-shutdown.flag` + `wsl-recycle-watch.bat`
  (the flag-watcher pattern already proven by `launch-chrome.flag`);
  register the watcher task when interop returns.

Pair with `wsl-docker-doctor` for diagnosis.
