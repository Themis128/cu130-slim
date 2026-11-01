#!/usr/bin/env python3
"""bridge_pin.py — hold the shared browser session via stack-ops keepawake
so seed+click runs survive session rotation.

    bridge_pin.py pin [ttl]   # keepawake browser-novnc (default 600s)
    bridge_pin.py unpin       # release (keepawake ttl=0)
"""
import json
import sys
import urllib.request

OPS = "http://stack-ops:8787"   # stack-ops control API


def _post(path: str) -> dict:
    r = urllib.request.Request(OPS + path, method="POST", data=b"")
    try:
        with urllib.request.urlopen(r, timeout=10) as f:
            return json.loads(f.read() or b"{}")
    except Exception as e:
        return {"error": str(e)}


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "pin"
    ttl = int(sys.argv[2]) if len(sys.argv) > 2 else 600
    if cmd == "pin":
        print(json.dumps(_post(f"/keepawake/browser-novnc?ttl={ttl}")))
    elif cmd == "unpin":
        print(json.dumps(_post("/keepawake/browser-novnc?ttl=0")))
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
