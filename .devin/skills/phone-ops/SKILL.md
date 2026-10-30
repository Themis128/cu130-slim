# phone-ops — S22+ remote control over Tailscale ADB

The S22+ (`galaxy-s22`, `100.84.31.85`) has wireless ADB on `:5555`. Trust is
already seeded (the Windows `adbkey` in `C:\Users\tbaltzakis\.android\` —
copy it to `~/.android/` on any Linux host).

## Quick ops

    adb connect 100.84.31.85:5555
    adb -s 100.84.31.85:5555 shell <cmd>

## adb_watch.py — reconnect + act when the phone comes back

Wireless ADB dies on every reboot until the phone is physically unlocked
once (adbd only starts after first unlock). `adb_watch.py` polls the phone,
reconnects the moment it responds, then optionally runs a command file:

    python3 adb_watch.py --script /sdcard/g2.sh        # run when back
    python3 adb_watch.py                              # just reconnect+report

## Gotchas learned live (2026-10-29)

- **Focus thieves**: `io.heckel.ntfy`, `com.instagram.android`, `ai.x.grok`,
  `com.wondershare.wutsapper.android`, `com.microsoft.appmanager` all steal
  top-app focus mid-flow. `heads_up_notifications_enabled 0` helps ntfy;
  `am force-stop` the rest before driving.
- **CoverHomeActivity**: a stuck cover-sensor state locks the screen as
  `com.android.systemui/.cover.CoverHomeActivity` — only clears on reboot.
- **Screen**: `settings put system screen_off_timeout 300000` keeps it
  awake during ops.
- **Deep links**: `https://www.tiktok.com/@user` VIEW intents resolve
  inconsistently (profile vs shared-video detail). The reliable path is
  cold-start → splash → main feed → Profile tab.
- `tiktok_profile_edit.py` (gates-ops) predates these guards — prefer
  driving with uiautomator dumps (`uiautomator dump` works on TikTok) +
  atomic on-device scripts that verify `topResumedActivity` between taps.
