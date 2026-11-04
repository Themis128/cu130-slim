#!/bin/bash
D=${1:-100.84.31.85:5555}; HOST=${D%%:*}
echo "== bootstrap $D =="
adb -s "$D" shell input keyevent KEYCODE_WAKEUP
adb -s "$D" shell am start -n com.termux/.app.TermuxActivity >/dev/null 2>&1; sleep 3
adb -s "$D" shell input text "sshd"; adb -s "$D" shell input keyevent ENTER; sleep 3
adb -s "$D" shell "netstat -an | grep -q :8022" || { echo "sshd not listening"; exit 1; }
K=$(awk "{print \$2}" /root/.ssh/id_ed25519.pub 2>/dev/null || awk "{print \$2}" /home/tbaltzakis/.ssh/id_ed25519.pub)
echo "ssh-ed25519 $K" > /tmp/ak.pub && adb -s "$D" push /tmp/ak.pub /sdcard/ak.pub >/dev/null 2>&1
T=/data/data/com.termux/files/home/.ssh
adb -s "$D" shell input text "mkdir%s-p%s${T}"; adb -s "$D" shell input keyevent ENTER; sleep 2
adb -s "$D" shell input text "cp%s/sdcard/ak.pub%s${T}/authorized_keys"; adb -s "$D" shell input keyevent ENTER; sleep 3
ssh -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=no -p 8022 "$HOST" hostname
