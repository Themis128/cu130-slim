#!/bin/bash
# phone-audit.sh — remotely verify the 3 Android toggles via ADB-over-tailscale.
PHONE=${1:-100.84.31.85}
echo "== phone-audit $PHONE =="
command -v adb >/dev/null || { echo "adb not installed — apt install adb"; exit 2; }
adb -s $PHONE connect "$PHONE" >/dev/null 2>&1
if ! adb devices | grep -q "$PHONE.*device"; then
  cat <<EOF
ADB unreachable — one-time setup on the phone:
  Settings > Developer options > Wireless debugging > ON
  then: adb -s $PHONE connect $PHONE:<pairing-port>
EOF
  exit 3
fi
aovpn=$(adb -s $PHONE shell settings get secure always_on_vpn_app 2>/dev/null | tr -d "\r")
[ "$aovpn" = "com.tailscale.ipn" ] && echo "PASS always-on VPN" || echo "FAIL always-on VPN = $aovpn"
adb -s $PHONE shell dumpsys deviceidle 2>/dev/null | grep -qi tailscale && echo "PASS battery unrestricted" || echo "FAIL battery optimized"
adb -s $PHONE shell dumpsys connectivity 2>/dev/null | grep -q "100.100.100.100" && echo "PASS Tailscale DNS" || echo "CHECK Tailscale DNS toggle in-app"
