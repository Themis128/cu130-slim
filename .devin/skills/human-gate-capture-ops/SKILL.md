# human-gate-capture-ops

Watch Downloads for credential drops → auto-provision the gated channel.
The flow that ends "waiting on user creds": **user drops a file → it wires
itself + shreds the file.**

## Drop conventions (Downloads, first non-# line = secret)

| File | Wires |
|---|---|
| `bsky*.txt` | `bsky_provision.py connect --handle cloudless.gr --password <secret>` |
| `viber*.txt` | `viber_provision.py --token <secret>` |

## Tool

```bash
python3 .devin/skills/human-gate-capture-ops/scripts/gate_capture.py scan     # one-shot
python3 .devin/skills/human-gate-capture-ops/scripts/gate_capture.py status
```

Cron: `*/15 * * * * python3 .../gate_capture.py scan >> ~/logs/gate-capture.log 2>&1`
(user crontab — installed 2026-11-11)

## Telegram drops (webhook → Redis → this consumer)

Message the bot `bsky <pw>` / `viber <tok>` / `whatsapp <token>` /
`meta <token>` (same regex as the file drops). The webhook can't run the
host provision scripts, and `getUpdates` polling 409s while the webhook
is registered — so `app/api/telegram.py` matches the pattern, LPUSHes
`{plat, secret_enc, ts, chat_id}` onto Redis list `gate:drops` (Fernet-
encrypted secret), and acks the sender. `gate_capture.py scan` RPOPs the
list inside `social-api` (decrypt happens in-container), provisions via
the same dispatch as file drops, and requeues failures ≤3 tries. Secret
never touches host disk or logs.

## Interview-coach key (already a non-gate)

The page seeds `sessionStorage.k` from `?key=` — hand the user:
`https://interview-coach.baltzakis-themis.workers.dev/?key=<AUTH_SECRET>`
One open = stored for the session. Current rotated key lives in the
`AUTH_SECRET` wrangler secret on `interview-coach`.

## Observability cancel (billing-plane — no API)

Can't be automated — dashboard-only. `observability_guard.py` pings the
exact clicks daily from Nov 20; after cancelling run
`observability_guard.py ack` to silence it.

## Extending the watcher

New gated channel? Add a `name.startswith(...)` branch in `scan()` calling
its provision script — the consume+shred-on-success pattern stays.

## The two floor-gates — tools

### `viber_phone_capture.py` — phone-in-app token via ADB

Phone plugged in + USB debugging → `viber_phone_capture.py run` opens the
Viber app, dumps the UI via `uiautomator`, extracts the token-shaped
string, provisions. `devices` checks attach first.

### `meta_payment_helper.py` — billing entry via logged-in session

`check` → is the Meta Business session live. `fill` (env-only card:
`META_CARD_NUMBER`/`META_CARD_EXP`/`META_CARD_CVC`) navigates to the
WhatsApp billing hub and maps the card fields — **fill is ours, the
submit click is yours** (PCI: card never stored, never printed).

### Watcher coverage (final)

| Input | Path |
|---|---|
| `bsky`/`viber`/`whatsapp`/`meta` tokens | Telegram msg or `*.txt` in Downloads → provisioned |
| Live sessions | auto-harvest on scan (bsky.app / partners.viber.com / business.facebook.com) |
| Payment state | `_waba_watch` polls → pings when block clears |
| Viber phone app | `viber_phone_capture.py run` when phone's on ADB |
| Meta billing | `meta_payment_helper.py fill` + your one confirm |
