# gate-cdp-runner

Daemon that closes every browser-side human gate the moment the debug Chrome
(`:9222`) exists — polls the CDP endpoint and runs each armed gate script once,
dropping results to the human-gate-capture watcher + Telegram.

## Gates it fires (each once, when CDP is up)

| Gate | Script | Output |
|---|---|---|
| LinkedIn restriction | `li_restriction_check.py` | reads banner — wait vs verify vs lifted |
| Bluesky app password | `bsky_apppw_gen.py` | creates one in bsky.app settings → `bsky.txt` drop |
| Viber bot token | `viber_webadmin.py` | extracts from webadmin.viber.com → `viber.txt` drop |
| Meta billing fill | `meta_payment_helper.py fill` | prefills fields (confirm stays human — payment floor) |

## Usage

```bash
nohup python3 .devin/skills/gate-cdp-runner/scripts/cdp_gate_runner.py &   # watch ~24h
python3 .../cdp_gate_runner.py --once    # fire now if CDP is up
python3 .../cdp_gate_runner.py --status  # armed vs done
```

State: `/tmp/cdp_gate_runner_state.json` — delete a key to re-arm a gate.

## Companion

`phone_ctl.py wa-greeting` (phone-automation-ops) — retry daemon for the
WA Business greeting toggle: attempts every 5 min for ~4h, exits when the
switch reads `checked`. Run `nohup phone_ctl.py wa-greeting &` whenever the
phone's plugged in.

## The remaining genuinely-human floors

- Smart Cover toggle (Settings→Accessibility) — needs one device tap; the
  `pm disable` on cover packages is the partial headless workaround.
- Meta card **confirm** — payment consent stays a human click by policy.
- LinkedIn restriction **wait** — the runner reports when it lifts.
