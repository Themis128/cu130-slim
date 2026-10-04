#!/usr/bin/env python3
"""gpu_serial.py — enforce the one-model-in-VRAM policy (flock-based GPU lock).

Policy (owner directive, 2026-10-04): only ONE model may be resident on the
8GB GPU at a time. Media creation is serialized: acquire the lock → unload
resident DMR models → run ONE job (generation OR QA, never both) → release.

Established architectures implementing this pattern (survey 2026-10-04):
  - llama-swap (github.com/mostlygeek/llama-swap): transparent hot-swap proxy
    in front of llama-server — unloads the current model when a request needs
    a different one. The productionized evolution of this script.
  - NVIDIA Triton `--model-control-mode=explicit` +
    `POST v2/repository/models/{model}/load|unload`.
  - DMR itself (one-at-a-time + idle timers + `docker model stop`).
  - diffusers: `enable_model_cpu_offload()` for the generation side.

Usage:
  python3 scripts/gpu_serial.py status
  python3 scripts/gpu_serial.py unload            # stop resident DMR models
  python3 scripts/gpu_serial.py run -- <cmd...>   # hold lock, stop DMR, run cmd
  GPU_SERIAL_LOCK=/tmp/gpu.lock python3 scripts/gpu_serial.py run -- ...
"""

import fcntl
import json
import os
import subprocess
import sys

LOCK_PATH = os.environ.get("GPU_SERIAL_LOCK", "/tmp/cu130-gpu.lock")


def _dmr(*args: str) -> str:
    return subprocess.run(
        ["docker", "model", *args], capture_output=True, text=True, check=False
    ).stdout


def resident_dmr_models() -> list[str]:
    """Names of DMR models currently loaded (docker model ps)."""
    out = _dmr("ps")
    names = []
    for line in out.splitlines()[1:]:  # skip header
        cols = line.split()
        if cols and not line.startswith("MODEL NAME"):
            names.append(cols[0])
    return names


def unload_dmr() -> list[str]:
    """Stop every resident DMR model; returns the names unloaded."""
    unloaded = []
    for name in resident_dmr_models():
        r = subprocess.run(
            ["docker", "model", "stop", name], capture_output=True, text=True
        )
        if "Unloaded" in (r.stdout + r.stderr):
            unloaded.append(name)
    return unloaded


def hold_and_run(cmd: list[str]) -> int:
    lock = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        unloaded = unload_dmr()
        if unloaded:
            print(json.dumps({"gpu_lock": "acquired", "unloaded": unloaded}),
                  file=sys.stderr)
        else:
            print(json.dumps({"gpu_lock": "acquired", "unloaded": []}),
                  file=sys.stderr)
        os.execvp(cmd[0], cmd)  # lock is released when the process exits
        return 1  # unreachable
    finally:
        try:
            fcntl.flock(lock, fcntl.LOCK_UN)
        except OSError:
            pass


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("action", choices=["status", "unload", "run"])
    ap.add_argument("cmd", nargs="*", help="command to run under the lock (action=run)")
    args = ap.parse_args()

    if args.action == "status":
        print(json.dumps({"resident": resident_dmr_models()}, indent=1))
        return 0
    if args.action == "unload":
        print(json.dumps({"unloaded": unload_dmr()}, indent=1))
        return 0
    if args.action == "run":
        if not args.cmd:
            print("run requires -- <command...>", file=sys.stderr)
            return 2
        return hold_and_run(args.cmd)
    return 2


if __name__ == "__main__":
    sys.exit(main())
