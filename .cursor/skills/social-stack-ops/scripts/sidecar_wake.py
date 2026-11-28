#!/usr/bin/env python3
"""sidecar_wake.py — wake an idle-slept stack-ops service from the host.

stack-ops stops idle-tolerant containers (browser sidecars etc.) after
~20 min and fronts in-cluster traffic with a TCP wake-proxy — but HOST
scripts that hit the direct published port (127.0.0.1:9226 etc.) get
connection-refused while the container sleeps. The control API on
127.0.0.1:8787 wakes by docker name regardless of network:

    POST /wake/<service>   -> start + wait healthy (STACK_OPS_WAKE_TIMEOUT)
    GET  /status/<name>    -> {state, ...}

Usage as a library:

    from sidecar_wake import wake_service, service_running
    if not service_running():
        wake_service("facebook-browser-sidecar")

CLI:  python3 sidecar_wake.py facebook-browser-sidecar [--timeout 120]
"""

import json
import os
import sys
import urllib.request

STACK_OPS = os.environ.get("STACK_OPS_URL", "http://localhost:8787")


def _req(method: str, path: str, timeout: float = 10.0):
    req = urllib.request.Request(
        STACK_OPS.rstrip("/") + path, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode() or "{}")


def service_state(name: str) -> str:
    """running | starting | stopped | exited | missing | unknown."""
    try:
        data = _req("GET", f"/status/{name}", timeout=8)
        return str(data.get("state") or "unknown")
    except Exception:  # noqa: BLE001
        return "unknown"


def service_running(name: str) -> bool:
    return service_state(name) in ("running", "unhealthy")


def wake_service(name: str, timeout: float = 130.0) -> bool:
    """Ask stack-ops to start <name> and wait until healthy.

    Returns True when the service reports running afterwards.
    """
    try:
        _req("POST", f"/wake/{name}", timeout=timeout)
    except Exception:  # noqa: BLE001 - fall through to state check
        pass
    return service_running(name)


if __name__ == "__main__":
    svc = sys.argv[1] if len(sys.argv) > 1 else "facebook-browser-sidecar"
    if wake_service(svc):
        print(f"{svc}: awake")
        sys.exit(0)
    print(f"{svc}: wake failed (state={service_state(svc)})", file=sys.stderr)
    sys.exit(1)
