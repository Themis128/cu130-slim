#!/bin/bash
# termux-drive.sh — push setup commands into a focused Termux window via ADB
# input injection. Works over the tailnet (adb connect <ip>:5555).
D=${1:-100.84.31.85:5555}
adb -s "$D" shell input keyevent KEYCODE_WAKEUP
adb -s "$D" shell am start -n com.termux/.app.TermuxActivity >/dev/null 2>&1
sleep 3
FOCUS=$(adb -s "$D" shell "dumpsys window | grep mFocusedApp" | grep -oE "com\.[a-z0-9.]+" | head -1)
if [ "$FOCUS" != "com.termux" ]; then
  echo "FOCUS=$FOCUS — open the flip cover / unlock so Termux is on screen, then re-run"; exit 3
fi
inject() { adb -s "$D" shell input text "$(echo "$1" | sed 's/ /%s/g;s/|/\|/g')"; sleep 1; adb -s "$D" shell input keyevent ENTER; sleep 4; }
inject "termux-wake-lock"
inject "pkg install -y openssh"
inject "sshd"
inject "mkdir -p ~/.termux/boot && printf '#!/data/data/com.termux/files/usr/bin/sh\ntermux-wake-lock\nsshd\n' > ~/.termux/boot/start-sshd.sh && chmod +x ~/.termux/boot/start-sshd.sh"
echo "termux-drive done — verify: ssh -p 8022 galaxy-s22.tail4ecae1.ts.net hostname"
