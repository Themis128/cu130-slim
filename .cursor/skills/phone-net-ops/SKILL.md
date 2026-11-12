---
name: phone-net-ops
description: Diagnose and repair the Galaxy S22+ notification stack end-to-end — per-app notification permissions, battery/doze exemptions, Tailscale exit-node PMTU blackholes that kill Slack's realtime socket, Slack session/pause state, ntfy websocket liveness. Use when phone alerts stop arriving, "socket is already closed" appears, or after any VPN/network change on the phone.
---

# phone-net-ops

One command diagnoses every layer that can silently kill phone notifications;
the exit-node tool fixes the one failure class that is invisible to app-level
checks. Both live in `home-ops/scripts/` (deployed at `~/scripts/`).

## Tools

| Tool | Purpose |
|---|---|
| `~/scripts/phone_net_diag.py` | Full-stack check: app install, `POST_NOTIFICATIONS` grant, `POST_NOTIFICATION`/`RUN_ANY_IN_BACKGROUND` appops, doze whitelist, exit-node route, PMTU DF-ping ladder, Slack session state, ntfy logcat liveness. `--fix` applies appops/whitelist/grant only. Exit 0 green / 1 findings / 2 unreachable. |
| `~/scripts/phone_exitnode.py` | `--off` / `--on <node>` toggles the phone's Tailscale exit node via uiautomator (no OCR). State is verified via `ip route` (`default dev tun0`), never trusted from the UI. |

## Failure classes this covers (all observed live 2026-11-12)

1. **Runtime permission revoked** — `POST_NOTIFICATIONS` shows
   `granted=false` (Slack had this; Android can auto-reset unused apps).
   Fix: `pm grant <pkg> android.permission.POST_NOTIFICATIONS`.
2. **Doze/background kill** — app missing from `dumpsys deviceidle
   whitelist` or `RUN_ANY_IN_BACKGROUND != allow`. Fix: `dumpsys
   deviceidle whitelist +<pkg>` + `appops set <pkg> RUN_ANY_IN_BACKGROUND allow`.
3. **Tailscale exit-node PMTU blackhole** — exit node ON (`default dev
   tun0`) + DF ping >~1228B lost. TCP survives small packets; Slack's
   realtime websocket stalls → "attempt 0 err socket is already closed" →
   zero notifications. Telegram/ntfy survive via FCM/HTTPS. Fix:
   `phone_exitnode.py --off` — keeps tailnet (ADB on 100.84.31.85 still
   works; that's tailnet-internal, not exit-node traffic).
4. **Slack logged out** — `topResumedActivity` = `SignInActivity`.
   Needs one manual sign-in (human gate).
5. **Slack "Notifications paused" snooze** — in-app pause survives all
   system fixes. Detect: profile menu shows "Notifications paused"; resume
   via the same menu → "Resume notifications". NOTE: this may be a
   deliberate user quiet-hours setting — confirm before resuming.
6. **ntfy credential-cache / stale endpoint** — see `ntfy-phone-ops`.

## Workflow

```bash
python3 ~/scripts/phone_net_diag.py           # find the broken layer
python3 ~/scripts/phone_net_diag.py --fix     # apply safe system fixes
python3 ~/scripts/phone_exitnode.py           # exit-node state
python3 ~/scripts/phone_exitnode.py --off     # heal Slack-class socket death
```

Then re-fire a test: `tg_alert.py ops "test"` (fans to ntfy too), a Slack
`chat.postMessage` via bot token, and `dumpsys notification --noredact |
grep pkg=<pkg>` on the phone to prove delivery.

## Gotchas

- ADB target: always `-s RFCTA0AEKZP`; wireless fallbacks
  `100.84.31.85:5555` / `192.168.1.77:5555`. USB can drop mid-session —
  `adb connect` the tailnet IP (works only while Tailscale is connected,
  exit node on or off).
- Samsung flip-cover: `CoverHomeActivity` focused = cover screen; the main
  display may still be ON — uiautomator dumps whichever is focused. If UI
  automation misfires, re-dump and re-check `mFocusedApp` before tapping.
- `pm list packages <name>` filtering is unreliable on this build — the
  diag script dumps the full list once instead.
- Never trust UI text for state that has a system source of truth
  (`ip route`, `dumpsys`) — the Tailscale "Disable/Enable" button label
  flips but the route table is the verdict.
