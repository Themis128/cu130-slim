#!/usr/bin/env python3
"""adb_sms.py — shared SMS-code reader over ADB (the proven path: phone on
USB/WiFi ADB, content provider query on the inbox)."""
import re
import subprocess

ADBS = ["adb",
        "/mnt/c/Users/tbaltzakis/AppData/Local/Android/Sdk/platform-tools/adb.exe",
        "/mnt/c/platform-tools/adb.exe"]
CODE_RE = re.compile(r"\b(\d{6})\b")


def read_code(sender_pat: str = r"viber|whatsapp|facebook|meta|AUTHMSG|partners",
              max_rows: int = 15) -> tuple[str | None, str]:
    """Return (code, matched_row) from the newest SMS matching the sender."""
    for adb in ADBS:
        try:
            r = subprocess.run(
                [adb, "shell", "content", "query", "--uri", "content://sms/inbox",
                 "--projection", "address,date,body", "--sort", "date DESC"],
                capture_output=True, text=True, timeout=10)
            rows = [l for l in r.stdout.splitlines()  # noqa: E741
                    if re.search(sender_pat, l, re.I)][:max_rows]
            for row in rows:
                m = CODE_RE.search(row)
                if m:
                    return m.group(1), row[:120]
        except Exception:
            continue
    return None, ""


if __name__ == "__main__":
    import sys
    code, row = read_code(sys.argv[1] if len(sys.argv) > 1 else
                          r"viber|whatsapp|facebook|meta|AUTHMSG|partners")
    print(code or "none", "::", row[:80])
