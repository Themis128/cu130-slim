# phone-adb-ops

Galaxy S22+ (SM-S906B) ADB access — two lanes, one entrypoint:
`scripts/phone_adb.py` (mirrored under `.cursor/skills/`).

## The two lanes

| Lane | Transport | adb binary | Serial |
|---|---|---|---|
| `usb` | USB-C cable | **Windows** `adb.exe` (WSL2 cannot see USB) | `RFCTA0AEKZP` |
| `net` | Tailscale wireless ADB | WSL `adb` (or same adb.exe) | `100.84.31.85:5555` |

**WSL2 has no USB passthrough** — the Linux `adb` never sees the cable.
Always route USB serials through the Windows platform-tools:

```
/mnt/c/Users/tbaltzakis/Android/Sdk/platform-tools/adb.exe
```

(`phone_adb.py` auto-detects it; `PHONE_ADB_EXE` overrides.)

## Usage

```bash
S=.devin/skills/phone-adb-ops/scripts/phone_adb.py
python3 $S devices                     # both lanes' live devices
python3 $S shell getprop ro.product.model
python3 $S --lane net shell wm size    # force tailnet
python3 $S tcpip                       # USB → re-enable wireless ADB
```

Auto-pick order: USB first, then tailnet `100.84.31.85:5555`, then
LAN `192.168.1.77:5555`, then one `adb connect` retry.

## Re-enabling wireless ADB

`adb tcpip 5555` only works over USB (or an existing session). Run
`phone_adb.py tcpip` with the cable attached — it switches adbd to TCP
mode and connects over tailnet. Wireless debugging toggles itself off
when the cable session ends (`adb_usb` mode); `tcpip` persists until
reboot or the wireless-debugging toggle is switched.

## Consumers

- `telegram-archive-ops/scripts/tg_login_phone.py` — same dual-lane
  logic inline (USB via adb.exe, tailnet fallback) for the Telegram
  login-code fetch.
- `phone-2fa-ops`, `phone-net-ops`, `phone-ui-automation` — read codes,
  TOTP, notifications via `dumpsys`/`uiautomator`; use `phone_adb.py
  shell …` or call `adb_bin()`/`serials()` from this script's dir.

## Gotchas

- Offline entries in `adb devices` are filtered (only `\tdevice` rows).
- `adb kill-server` on Windows does NOT affect the WSL adb server —
  they are separate daemons with separate device lists.
- Phone battery: wireless ADB dies with doze; USB is the reliable lane
  for long sessions. Keep the cable for login/2FA flows.
- Never commit serials/keys — `~/.android/adbkey*` stays on the host.
