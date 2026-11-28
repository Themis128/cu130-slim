#!/usr/bin/env bash
# Classify the phone's Meta-app Messenger surface state via ADB.
# Reports: ADB liveness, foreground app, active Messenger profile,
# Business Suite presence, and the cosmetic "Waiting for network" banner.
#
# Usage: messenger_phone_check.sh [--dump]
#   --dump   also uiautomator-dump the current screen to /tmp/window.xml

set -u
ADB="${ADB:-adb}"
PHONE="${PHONE:-100.84.31.85:5555}"   # Galaxy S22+ on tailnet

die() { echo "FAIL: $*" >&2; exit 1; }

# --- 1. ADB liveness -------------------------------------------------------
$ADB -s "$PHONE" get-state >/dev/null 2>&1 || {
    echo "ADB down on $PHONE — trying reconnect..."
    $ADB connect "$PHONE" >/dev/null 2>&1
    $ADB -s "$PHONE" get-state >/dev/null 2>&1 || \
        die "ADB unreachable. Wireless debugging may be OFF (human gate: toggle in Developer options)."
}

# --- 2. Package presence ----------------------------------------------------
pkgs=$($ADB -s "$PHONE" shell pm list packages 2>/dev/null)
echo "$pkgs" | grep -q "com.facebook.orca" \
    && echo "Messenger app (orca): INSTALLED" || echo "Messenger app: MISSING"
echo "$pkgs" | grep -q "com.facebook.pages.app" \
    && echo "Meta Business Suite: INSTALLED" || echo "Meta Business Suite: MISSING"

# --- 3. Foreground app ------------------------------------------------------
fg=$($ADB -s "$PHONE" shell dumpsys activity activities 2>/dev/null \
    | grep -oE "mResumedActivity[^ ]* *[^ ]+" | head -1)
echo "Foreground: ${fg:-unknown}"

# --- 4. Banner + profile (requires Messenger foregrounded) -------------------
if [ "${1:-}" = "--dump" ]; then
    $ADB -s "$PHONE" shell uiautomator dump /sdcard/window.xml >/dev/null 2>&1
    $ADB -s "$PHONE" pull /sdcard/window.xml /tmp/window.xml >/dev/null 2>&1
    echo "Dumped: /tmp/window.xml"
fi
xml=$($ADB -s "$PHONE" shell uiautomator dump /dev/tty 2>/dev/null \
    | tail -1)
[ -z "$xml" ] && xml=$($ADB -s "$PHONE" shell cat /sdcard/window.xml 2>/dev/null)

if [ -n "$xml" ]; then
    echo "$xml" | grep -qi "Waiting for network" \
        && echo "Banner: 'Waiting for network' PRESENT (cosmetic — clears on profile switch; see SKILL.md)" \
        || echo "Banner: absent"
    echo "$xml" | grep -oE "Logged in as[^\"]*|cloudless\.gr|Themistoklis Baltzakis" \
        | sort -u | sed 's/^/Profile hint: /'
fi

# --- 5. Tailscale split-tunnel check ---------------------------------------
ts=$($ADB -s "$PHONE" shell dumpsys package com.tailscale.ipn 2>/dev/null \
    | grep -c versionName)
[ "$ts" -gt 0 ] && echo "Tailscale: present — verify orca is in split-tunnel exclusions (Meta sockets fragile under MTU-1280)"
