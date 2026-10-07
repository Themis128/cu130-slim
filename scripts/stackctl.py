#!/usr/bin/env python3
"""stackctl — control the stack-ops idle-sleep/wake proxy.

Usage:
  python3 scripts/stackctl.py status                 # all managed services
  python3 scripts/stackctl.py wake <name>            # start + wait healthy
  python3 scripts/stackctl.py sleep <name>           # stop now
  python3 scripts/stackctl.py keepawake <name> [ttl] # don't auto-sleep (default 2h)
  python3 scripts/stackctl.py savings                # memory freed by sleeping
"""

import json
import subprocess
import sys
import urllib.request

API = "http://127.0.0.1:8787"


def _req(method: str, path: str) -> dict:
    r = urllib.request.Request(API + path, method=method)
    try:
        with urllib.request.urlopen(r, timeout=125) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return {"error": str(e)}


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "status":
        for name, info in _req("GET", "/status").items():
            ago = info.get("last_active_ago_s")
            ago_s = f"{ago}s ago" if ago is not None else "never"
            print(f"{info['state']:>9}  conns={info['active_conns']:<2} "
                  f"last-active={ago_s:<10} keepawake={info['keepawake_s']}s  {name}")
    elif cmd == "wake":
        print(json.dumps(_req("POST", f"/wake/{sys.argv[2]}"), indent=2))
    elif cmd == "sleep":
        print(json.dumps(_req("POST", f"/sleep/{sys.argv[2]}"), indent=2))
    elif cmd == "keepawake":
        ttl = sys.argv[3] if len(sys.argv) > 3 else "7200"
        print(json.dumps(_req("POST", f"/keepawake/{sys.argv[2]}?ttl={ttl}"), indent=2))
    elif cmd == "savings":
        out = subprocess.run(
            ["docker", "stats", "--no-stream", "--format",
             "{{.Name}}\t{{.MemUsage}}"],
            capture_output=True, text=True).stdout
        managed = set(_req("GET", "/status"))
        used = 0.0
        for line in out.splitlines():
            name, _, mem = line.partition("\t")
            if name not in managed:
                continue
            num, _, unit = mem.split(" /")[0].partition("MiB")
            if "GiB" in mem:
                used += float(num.strip()) * 1024
            else:
                try:
                    used += float(num.strip())
                except ValueError:
                    pass
        print(f"sleep-managed containers currently using ~{used:.0f} MiB")
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
