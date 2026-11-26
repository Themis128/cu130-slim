# Matrix lane for personal Messenger (mautrix-meta → continuwuity)

Durable replacement for the browser-sidecar personal-Messenger lane.
The mautrix-meta bridge holds the FB session at **protocol level**
(Lightspeed/messagix, E2EE-safe via Armadillo) — no browser, no DOM
clicks, immune to the password-change/logout class that kills the
Playwright sidecar.

## Topology

```
Messenger ⇄ mautrix-meta (dock.mau.dev/mautrix/meta) ⇄ continuwuity
              protocol-level, ghost users @facebook_*:socialauto.internal
                                                   ⇅ Matrix client API
                              social-worker-messenger:
                  app.worker.tasks.matrix_messenger.poll_matrix_messenger
                              (60s beat, queue=messenger)
```

- `continuwuity` — bridge-only homeserver, `socialauto.internal`,
  **no federation, no host ports** (cu130-backend only).
- `mautrix-meta` — appservice `facebook`, bot `@facebookbot`,
  sqlite at `./matrix/mautrix-meta/`.
- Secrets: `MATRIX_ADMIN_TOKEN`, `MATRIX_ADMIN_PASSWORD`,
  `MATRIX_ADMIN_USER` (`@socialauto:socialauto.internal`) in
  `social_secrets` — bridge-linked user token.
- `.env` `MATRIX_REGISTRATION_TOKEN` — only for creating NEW users.

## Operations

**Status**: `docker logs mautrix-meta --tail 20` (look for
`Starting network connector` / messagix cursor updates);
`docker logs continuwuity --tail 10`.

**Re-login after Meta password change / session death**: DM
`@facebookbot` via the admin token — createRoom(is_direct, invite
facebookbot) → send `login facebook` → send cookie JSON
(`GET facebook-browser-sidecar:9226/debug/all-cookies`, or from the
user's live Chrome via CDP `Network.getAllCookies`). No password/2FA
needed when a logged-in browser session exists anywhere on the host.

**Admin room**: `!admin appservices list` / `register` — appservice
registered via admin room message with `matrix/mautrix-meta/registration.yaml`.

**Adapter**: `matrix_messenger.py` — incremental `/sync` (since-token
in Redis `matrix:messenger:sync_since`), newest-msg-from-ghost check
per portal room, then the shared reply pipeline (cooldown → pause →
intent → brand RAG → `generate_contextual_reply` DMR-first).
Sends typing + reply as `@socialauto`; bridge forwards to Messenger.
First-ever run stores the since-token only (bootstrap — never
answers stale history). Seen state:
`meta_data.matrix_messenger_seen[room_id]` (separate from the
browser lane's `personal_messenger_seen`).

## Notes / gotchas

- The bridge also supports `login messenger` (messenger.com cookies)
  and `messenger-lite` (username/password, iOS API) flows.
- `network.e2ee_backfill` is off; encrypted history upload needs the
  FB security PIN — enable + supply PIN if needed.
- Portal rooms: personal DMs only by default; group threads bridge too.
- RAM budget: continuwuity ~100-150MB, mautrix-meta ~150-250MB.
- Browser-sidecar lane still runs in parallel (kept for
  profile/page ops); the two `seen` maps don't clash.
