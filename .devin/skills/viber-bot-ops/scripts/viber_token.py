#!/usr/bin/env python3
"""viber_token.py — Viber App Token provisioning. partners.viber.com is
RETIRED (dead site, in-app management only) — the token comes from the
Viber mobile app's bot-edit panel. This tool: --check reports connection
state, --token <t> runs viber_provision directly."""
import os
import subprocess
import sys


def _connected() -> bool:
    r = subprocess.run(["docker", "compose", "exec", "-T", "social-api",
                        "python", "scripts/viber_tool.py", "accounts"],
                       capture_output=True, text=True, timeout=30)
    return bool(r.stdout.strip()) and "no viber" not in r.stdout.lower()


def _provision(token: str) -> str:
    r = subprocess.run(["python3", ".devin/skills/viber-ops/scripts/viber_provision.py",
                        "--token", token, "--name", "cloudless"],
                       capture_output=True, text=True, timeout=60)
    return (r.stdout + r.stderr)[:200]


def main() -> int:
    if "--token" in sys.argv:
        tok = sys.argv[sys.argv.index("--token") + 1]
        print("provision:", _provision(tok))
        return 0
    print("viber:", "connected" if _connected() else "no accounts — token from the Viber app (bot → Edit Info)")
    return 0 if _connected() else 1


if __name__ == "__main__":
    sys.exit(main())
