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

Cron: `*/15 * * * * python3 .../gate_capture.py scan >> /tmp/gate_capture.log 2>&1`

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
