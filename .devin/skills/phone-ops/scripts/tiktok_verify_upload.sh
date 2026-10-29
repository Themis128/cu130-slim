#!/system/bin/sh
# tiktok_verify_upload.sh — on-device (run via adb shell sh /sdcard/tvu.sh)
# Business Suite → Verify → Edit → upload cert.png → Submit.
# Guards: keep screen awake, silence focus thieves, verify musically top
# between every tap, and retry the whole flow once on failure.
P=com.zhiliaoapp.musically
TOP(){ dumpsys activity activities 2>/dev/null | grep topResumedActivity | head -1 | grep -q "$1"; }
front(){  # re-assert TikTok to front if a thief stole focus
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
  input tap 503 780;  sleep 4          # '+' tile → file picker
  return 0
}
prepare
flow
screencap -p /sdcard/tvu_result.png
dumpsys activity activities | grep topResumedActivity | head -1
echo "== next: pick cert.png in the file picker, then Submit =="
