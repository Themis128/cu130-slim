#!/system/bin/sh
# tiktok_verify_upload.sh — on-device (run via adb shell sh /sdcard/tvu.sh)
# Business Suite → Verify → Edit → upload cert.png → Submit.
# Guards: screen awake, thieves silenced, musically-top verified between taps.
P=com.zhiliaoapp.musically
TOP(){ dumpsys activity activities 2>/dev/null | grep topResumedActivity | head -1 | grep -q "$1"; }
front(){
  i=0; until TOP musically || [ $i -gt 20 ]; do
    am start --activity-reorder-to-front -n $P/com.ss.android.ugc.aweme.splash.SplashActivity >/dev/null 2>&1
    sleep 0.6; i=$((i+1)); done
}
prepare(){
  settings put system screen_off_timeout 300000
  settings put global heads_up_notifications_enabled 0
  for t in io.heckel.ntfy com.instagram.android ai.x.grok \
           com.wondershare.wutsapper.android com.microsoft.appmanager; do
    am force-stop $t
  done
}
# tap the node whose text/desc contains $1 in the last uiautomator dump
tap_node(){  # $1=substring — taps center of its bounds
  B=$(grep -oE 'bounds="[^"]*"' /sdcard/u.xml | \
      grep -oE '\[[0-9]+,[0-9]+\]\[[0-9]+,[0-9]+\]' | head -1)
  :
}
# pick an image in the system picker: try desc/text match on cert.png,
# else open the Downloads listing and tap the first file row.
pick_cert(){
  for i in 1 2 3 4 5 6 7 8; do
    uiautomator dump /sdcard/u.xml >/dev/null 2>&1
    # exact cert.png match anywhere (text or content-desc)
    LN=$(grep -n "cert.png\|cert" /sdcard/u.xml | head -1)
    if [ -n "$LN" ]; then
      B=$(sed -n "${LN}p" /sdcard/u.xml | grep -oE '\[[0-9]+,[0-9]+\]\[[0-9]+,[0-9]+\]' | head -1)
      if [ -n "$B" ]; then
        X=$(echo "$B" | tr -d '[]' | tr ',' ' ' | awk '{print int(($1+$3)/2)}')
        Y=$(echo "$B" | tr -d '[]' | tr ',' ' ' | awk '{print int(($2+$4)/2)}')
        input tap $X $Y
        echo "picked cert.png at $X,$Y"
        return 0
      fi
    fi
    # row of Downloads side-tab or "Show roots"/"Browse" entry
    for W in Downloads "Show roots" "Browse" Files; do
      LN=$(grep -n ">$W<" /sdcard/u.xml | head -1)
      if [ -n "$LN" ]; then
        B=$(sed -n "${LN}p" /sdcard/u.xml | grep -oE '\[[0-9]+,[0-9]+\]\[[0-9]+,[0-9]+\]' | head -1)
        X=$(echo "$B" | tr -d '[]' | tr ',' ' ' | awk '{print int(($1+$3)/2)}')
        Y=$(echo "$B" | tr -d '[]' | tr ',' ' ' | awk '{print int(($2+$4)/2)}')
        input tap $X $Y; sleep 1.5
      fi
    done
    sleep 1.5
  done
  echo "picker: cert.png not found — dump at /sdcard/u.xml"
  return 1
}
flow(){
  am force-stop $P; sleep 1
  am start -n $P/com.ss.android.ugc.aweme.splash.SplashActivity >/dev/null 2>&1
  i=0; until TOP musically || [ $i -gt 60 ]; do sleep 0.3; i=$((i+1)); done
  TOP musically || return 1
  sleep 6
  input tap 972 2160; sleep 3; front   # Profile tab
  input tap 610 724;  sleep 3; front   # Business Suite
  input tap 801 902;  sleep 4; front   # Verify
  input tap 183 548;  sleep 2; front   # Edit (rejected banner)
  input tap 337 1307; sleep 3; front   # Edit confirm dialog
  input tap 540 2140; sleep 3; front   # Next on basic info
  input tap 503 780;  sleep 4          # '+' tile → picker
  pick_cert && { sleep 3; input tap 540 2140; }  # Submit
  return 0
}
prepare
flow
screencap -p /sdcard/tvu_result.png
dumpsys activity activities | grep topResumedActivity | head -1
echo "== tvu done =="
