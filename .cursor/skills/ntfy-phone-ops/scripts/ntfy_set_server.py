#!/usr/bin/env python3
"""ntfy_set_server.py — repoint the ntfy Android app to a working server via ADB.

    ntfy_set_server.py http://100.74.191.58:30080 \
        --topic cloudless-alerts --user phone --password-env NTFY_PHONE_PW

Drives the real ntfy UI (validated on io.heckel.ntfy / One UI 8):

  1. Picks a real ADB device (skips emulators/offline entries).
  2. Wakes the screen, launches ntfy via monkey (handles .ui.MainActivity
     vs .MainActivity across builds) and VERIFIES it holds foreground —
     never taps blindly into whatever app happens to be up.
  3. Settings -> Default server -> clear + type URL -> SAVE.
  4. Deletes stale subscriptions (long-press row -> "Unsubscribe" ->
     "Delete permanently").
  5. Re-adds the topic via the "Add subscription" FAB, filling the
     "Login required" dialog when the server enforces auth
     (our ntfy runs auth_default_access: deny-all).
  6. Verifies the topic screen shows <host>/<topic> with no
     "Connection error", optionally publishes a test message.

If the server IP is on the tailnet and the phone can't ping it, the
script fires the Tailscale CONNECT_VPN broadcast and retries.
"""
import argparse
import re
import subprocess
import sys
import time
import urllib.request

PKG = "io.heckel.ntfy"
SERIAL = ""


def adb(*args: str, timeout: int = 30) -> str:
    cmd = ["adb"] + (["-s", SERIAL] if SERIAL else []) + list(args)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r.stdout


def check_adb() -> None:
    """Pick a real online device — prefer non-emulator when several attach."""
    global SERIAL
    if SERIAL:
        return
    out = subprocess.run(["adb", "devices"], capture_output=True,
                         text=True, timeout=30).stdout
    serials = [l.split()[0] for l in out.splitlines()[1:]  # noqa: E741
               if len(l.split()) >= 2 and l.split()[1] == "device"]
    if not serials:
        raise SystemExit("no adb device — check wireless ADB / phone-access")
    SERIAL = next((s for s in serials if not s.startswith("emulator")),
                  serials[0])
    print(f"device: {SERIAL}")


def dump() -> str:
    return adb("exec-out", "uiautomator", "dump", "/dev/tty")


def _tap_xy(m: re.Match) -> None:
    x, y = (int(m[1]) + int(m[3])) // 2, (int(m[2]) + int(m[4])) // 2
    adb("shell", "input", "tap", str(x), str(y))
    time.sleep(1.5)


def tap_any(label: str, xml: str | None = None) -> bool:
    """Tap element whose text OR content-desc contains label."""
    xml = xml or dump()
    pat = re.compile(
        rf'(?:text|content-desc)="[^"]*{re.escape(label)}[^"]*"'
        rf'[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', re.IGNORECASE)
    m = pat.search(xml)
    if not m:
        return False
    _tap_xy(m)
    return True


def edit_fields(xml: str | None = None) -> list[tuple[int, int]]:
    xml = xml or dump()
    out = []
    for f in re.findall(
            r'class="android.widget.EditText"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"',
            xml):
        out.append(((int(f[0]) + int(f[2])) // 2, (int(f[1]) + int(f[3])) // 2))
    return out


def type_into(x: int, y: int, text: str, clear: bool = True) -> None:
    adb("shell", "input", "tap", str(x), str(y))
    time.sleep(1)
    if clear:
        adb("shell", "input", "keyevent", "KEYCODE_MOVE_END")
        adb("shell", "for i in $(seq 60); do input keyevent 67; done")
        time.sleep(0.5)
    adb("shell", "input", "text", text.replace(" ", "%s"))
    time.sleep(1)


def foreground_pkg() -> str:
    out = adb("shell", "dumpsys", "window", "displays")
    m = re.search(r"mCurrentFocus=Window\{[^}]*\s(\S+)/", out)
    return m.group(1) if m else ""


def wake_and_launch() -> None:
    adb("shell", "input", "keyevent", "KEYCODE_WAKEUP")
    time.sleep(1)
    if adb("shell", "dumpsys", "window").find("mDreamingLockscreen=true") >= 0:
        # swipe up; a PIN prompt stays a human gate
        adb("shell", "input", "swipe", "540", "1800", "540", "400", "300")
        time.sleep(1.5)
    out = adb("shell", "monkey", "-p", PKG, "-c",
              "android.intent.category.LAUNCHER", "1")
    if "No activities found" in out:
        raise SystemExit(f"{PKG} has no launcher activity — is ntfy installed?")
    for _ in range(10):
        time.sleep(1)
        if foreground_pkg() == PKG:
            return
    raise SystemExit(
        f"{PKG} did not come to foreground (on: {foreground_pkg()}) — aborting "
        "rather than tapping the wrong app")


def phone_ping(host: str) -> bool:
    ip = re.sub(r":.*", "", host)
    r = adb("shell", "ping", "-c", "1", "-W", "3", ip, timeout=10)
    return "1 received" in r or "bytes from" in r


def ensure_tailscale(host: str) -> None:
    if phone_ping(host):
        return
    print(f"phone can't reach {host} — enabling Tailscale VPN")
    adb("shell", "am", "broadcast", "-a", "com.tailscale.ipn.CONNECT_VPN",
        "-n", "com.tailscale.ipn/.IPNReceiver")
    for _ in range(8):
        time.sleep(2)
        if phone_ping(host):
            print("tailnet up")
            return
    raise SystemExit(f"phone still can't reach {host} after VPN toggle")


def set_default_server(url: str) -> None:
    if not tap_any("More options"):
        raise SystemExit("More options not found")
    if not tap_any("Settings"):
        raise SystemExit("Settings entry not found")
    if not tap_any("Default server"):
        raise SystemExit("Default-server row not found in settings")
    fields = edit_fields()
    if not fields:
        raise SystemExit("no EditText in Default-server dialog")
    type_into(*fields[0], url)
    if not tap_any("SAVE"):
        raise SystemExit("SAVE button not found")
    adb("shell", "input", "keyevent", "KEYCODE_BACK")
    time.sleep(1.5)


def delete_all_subscriptions() -> int:
    removed = 0
    for _ in range(5):
        xml = dump()
        if "don't have any subscriptions" in xml:
            break
        m = re.search(
            r'text="[^"]+/[^"]+"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"',
            xml)
        if not m:
            break
        x, y = (int(m[1]) + int(m[3])) // 2, (int(m[2]) + int(m[4])) // 2
        adb("shell", "input", "swipe", str(x), str(y), str(x), str(y), "1200")
        time.sleep(1.5)
        if not tap_any("Unsubscribe"):
            break
        if not tap_any("Delete permanently"):
            break
        removed += 1
        time.sleep(1)
    return removed


def add_subscription(topic: str, user: str | None, pw: str | None) -> None:
    if not tap_any("Add subscription"):
        raise SystemExit("Add-subscription FAB not found")
    fields = edit_fields()
    if not fields:
        raise SystemExit("no topic field in subscribe dialog")
    type_into(*fields[0], topic, clear=False)
    if not tap_any("SUBSCRIBE"):
        raise SystemExit("SUBSCRIBE not found")
    xml = dump()
    if "Subscribe to topic" in xml:  # first tap only dismissed the keyboard
        tap_any("SUBSCRIBE")
        xml = dump()
    if "Login required" in xml:
        if not (user and pw):
            raise SystemExit(
                "server requires auth — rerun with --user + --password-env")
        fields = edit_fields(xml)
        if len(fields) < 2:
            raise SystemExit("login dialog has no user/pass fields")
        type_into(*fields[0], user, clear=False)
        type_into(*fields[1], pw, clear=False)
        if not tap_any("LOG IN"):
            raise SystemExit("LOG IN button not found")
        time.sleep(2)


def verify(topic: str) -> bool:
    xml = dump()
    return f"/{topic}" in xml and "Connection error" not in xml


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("server", help="base URL, e.g. http://100.74.191.58:30080")
    ap.add_argument("--topic", default="cloudless-alerts")
    ap.add_argument("--user", help="ntfy username (auth-enabled servers)")
    ap.add_argument("--password-env", default="NTFY_PHONE_PW",
                    help="env var holding the ntfy password (not argv)")
    ap.add_argument("--test", action="store_true",
                    help="publish a test message after subscribing")
    a = ap.parse_args()
    if not a.server.startswith(("http://", "https://")):
        ap.error("server must be http(s)://host[:port]")
    import os
    pw = os.environ.get(a.password_env, "")

    check_adb()
    host = a.server.split("://", 1)[1]
    ensure_tailscale(host)
    wake_and_launch()

    xml = dump()
    if "Connection error" in xml:
        tap_any("Close")
        time.sleep(1)

    set_default_server(a.server)
    n = delete_all_subscriptions()
    print(f"removed {n} stale subscription(s)")
    add_subscription(a.topic, a.user, pw)

    if not verify(a.topic):
        raise SystemExit("topic screen missing or Connection error persists")
    print(f"subscribed: {a.server}/{a.topic}")

    if a.test:
        try:
            req = urllib.request.Request(
                f"{a.server.rstrip('/')}/{a.topic}",
                data=b"ntfy_set_server test ping", method="POST")
            if a.user and pw:
                import base64
                cred = base64.b64encode(f"{a.user}:{pw}".encode()).decode()
                req.add_header("Authorization", f"Basic {cred}")
            urllib.request.urlopen(req, timeout=10)
            time.sleep(3)
            xml = dump()
            print("test message on phone:", "test ping" in xml)
        except Exception as e:
            print(f"test publish failed: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
