# phone-automation-ops

ADB UI-drive for the user's attached Android phone (Samsung, `RFCTA0AEKZP`) —
app install/config, screen unlock, and resilient automation patterns.

## ADB access

- **Binary:** `/mnt/c/Users/tbaltzakis/AppData/Local/Android/Sdk/platform-tools/adb.exe` (Windows side)
- **Always target the device:** `adb -s RFCTA0AEKZP …` — the dead `emulator-5554` is always listed; unqualified calls fail with "more than one device/emulator"
- WSL `UtilAcceptVsock` errors = the WSL↔Windows interop dying — `wsl --shutdown` in a real Windows PowerShell, reopen session

## Screen/cover lock (the automation killer)

- `mFocusedApp=…systemui/.cover.CoverHomeActivity` = Samsung smart-cover screen is showing — uiautomator dumps go stale and taps land nowhere
- **Fix applied:** `pm disable-user --user 0 com.sec.android.cover.ledcover` (+ `com.samsung.android.app.ledbackcover`, `com.samsung.android.app.ledcoverdream`) — kills the cover screen handler
- The OneUI path is Settings → Accessibility → Smart Cover (user toggle); the `pm disable` path is the headless equivalent
- Wake/unlock sequence: `input keyevent 224` (wake) → `input swipe 540 2000 540 400 300` (swipe up) — verifies against `dumpsys window | grep mFocusedApp`
- `svc power stayon true` / `stay_on_while_plugged_in=15` keeps the screen on while plugged

## App management

- **Inventory:** `pm list packages <pkg>` — instant yes/no, beats UI navigation
- **Install via Play Store:** `am start -a android.intent.action.VIEW -d "market://details?id=<pkg>" -p com.android.vending` — the `-p` flag matters; without it the store opens to home/ads and swallows the deep link
- "Open with" dialog (first market:// open) — pick **Google Play Store** → "Just once"
- **Don't tap "Install" on a promo card** — Play Store shows a sponsored app with its own Install button above the real one; verify the page title first

## UI-drive pattern (uiautomator)

```python
dump = adb("shell uiautomator dump /sdcard/ui.xml && cat /sdcard/ui.xml")
# extract  text="X" ... bounds="[l,t][r,b]"  → tap center ((l+r)/2,(t+b)/2)
```
- Dump then grep the bounds of the target `text=`/`content-desc=` — never hardcode coordinates beyond the one-time discovery
- `input tap x y`, `input swipe x1 y1 x2 y2`, `input keyevent 4` (back), `82` (menu), `224` (wake)
- Cover screen or dumpsys window focus = gate every step

## What's configured on this phone

- Nextcloud: fully set up (Cloudless account, syncing)
- WhatsApp Business: installed + chats live — Settings → Business tools nav mapped (profile row → business tools → greeting/away/hours)
- Viber: **no Bots section in Settings** (verified — current builds moved it to `webadmin.viber.com`)
- Meta Business Suite: installed 2026-10-30

## Tool

`scripts/phone_ctl.py` — wraps everything above: `devices`, `unlock`, `cover-off`, `install <pkg>`, `ui-tap <text>`, `dump`.
