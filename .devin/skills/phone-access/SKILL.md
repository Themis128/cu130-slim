---
name: phone-access
description: Access the Galaxy S22+ — ADB/uiautomator control + Termux SSH. `scripts/phone-mcp-server.py` exposes a phone MCP server for tool-driven UI automation (tap, text, dump, launch). Use alongside the phone-settings/oneui-drawer skills for drawer/settings work, and phone-termux for notification/clipboard/SMS reads.
---

# phone-access

Galaxy S22+ access layer. ADB + uiautomator2 drive the UI; Termux SSH
(`ssh -p 8022 u0_a357@192.168.1.77`) handles notification, clipboard, SMS,
and file ops.

## Scripts

- `scripts/phone-mcp-server.py` — the MCP server exposing ADB +
  uiautomator2 control (screen dump, tap, swipe, text, app launch, key
  events). Register in `.devin/mcp_config.json` for tool-driven
  automation; or call its helpers directly from scripts.

## Notes

- Wireless ADB can drop mid-session — re-arm via USB or the phone's
  developer options; the device id may change between USB and WiFi modes.
- Lock orientation to portrait before coordinate taps
  (`settings put system accelerometer_rotation 0; user_rotation 0`) —
  landscape breaks all coordinates.
- `uiautomator dump` path args get MSYS-mangled in Git Bash — use
  `exec-out screencap -p > file.png` for screenshots instead.
- Field-entry caveat learned the hard way: `input text` APPENDS at the
  cursor — use the field's own ✕ (clear) button first, never backspaces.
