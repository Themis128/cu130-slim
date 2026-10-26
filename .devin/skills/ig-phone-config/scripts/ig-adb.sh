#!/usr/bin/env bash
# IG ADB helpers — device RFCTA0AEKZP / 192.168.1.77:5555
set -u
D=${ADB_SERIAL:-192.168.1.77:5555}
A="adb -s $D"

unlock(){ $A shell input keyevent KEYCODE_WAKEUP; sleep 1; $A shell input keyevent 82; $A shell input swipe 540 1800 540 400 300; }
shot(){ $A exec-out screencap -p > "$1"; }
focused(){ $A shell dumpsys window | grep -oE 'mFocusedApp=[^ ]*' | head -1; }
covered(){ focused | grep -q CoverHomeActivity; }
ui(){ $A shell "uiautomator dump /sdcard/ui.xml" >/dev/null; MSYS_NO_PATHCONV=1 $A shell "cat /sdcard/ui.xml" 2>/dev/null; }
bounds(){ ui | python -X utf8 -c 'import sys,re;x=sys.stdin.read();t=sys.argv[1];m=re.search(r"text=\""+re.escape(t)+r"\"[^>]*bounds=\"\[(\d+),(\d+)\]\[(\d+),(\d+)\]\"",x);print(f"{int(m.group(1))+int(m.group(3))//2} {int(m.group(2))+int(m.group(4))//2}" if m else "NOTFOUND")' "$1"; }
tap_text(){ read -r x y < <(bounds "$1"); [ "$x" = NOTFOUND ] && return 1; $A shell input tap "$x" "$y"; }
# clean-replace a focused text field: select-all, cut, type (ASCII + %s spaces)
type_field(){ $A shell input swipe 400 "$2" 400 "$2" 1200 >/dev/null; sleep 1; $A shell input keyevent 29 >/dev/null; sleep 1; $A shell input text "$1"; }
case "${1:-}" in unlock) unlock;; shot) shot "$2";; focused) focused;; covered) covered;; bounds) bounds "$2";; tap_text) tap_text "$2";; *) echo "unlock|shot|focused|covered|bounds|tap_text";;
esac
