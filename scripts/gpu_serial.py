#!/usr/bin/env python3
"""gpu_serial.py — enforce the one-model-in-VRAM policy (flock-based GPU lock).

Policy (owner directive, 2026-10-04): only ONE model may be resident on the
8GB GPU at a time. Media creation is serialized: acquire the lock → unload
resident DMR models → run ONE job (generation OR QA, never both) → release.

DMR control went HTTP-only (2026-10-05): the `docker model` CLI plugin
I/O-errors inside containers, so unload/list now go through
`/api/ps` + `/api/chat keep_alive:0` / `/inference/unload` — the same
paths app/services/dmr.py uses. Server-side, `app/services/gpu_arbiter.py`
implements the same arbitration for celery media tasks.

Established architectures implementing this pattern (survey 2026-10-04):
  - llama-swap (github.com/mostlygeek/llama-swap): transparent hot-swap proxy
    in front of llama-server — unloads the current model when a request needs
    a different one. The productionized evolution of this script.
  - NVIDIA Triton `--model-control-mode=explicit` +
    `POST v2/repository/models/{model}/load|unload`.
  - DMR itself (one-at-a-time + idle timers + HTTP unload).
  - diffusers: `enable_model_cpu_offload()` for the generation side.

Usage:
  python3 scripts/gpu_serial.py status
  python3 scripts/gpu_serial.py unload            # unload resident DMR models
  python3 scripts/gpu_serial.py run -- <cmd...>   # hold lock, stop DMR, run cmd
  GPU_SERIAL_LOCK=/tmp/gpu.lock python3 scripts/gpu_serial.py run -- ...
"""

import argparse
import fcntl
import json
import os
import subprocess
import sys
import urllib.request

# Docker Desktop publishes the runner on 12435 on this host (12434 is the
# DMR default; override with DMR_HOST_URL if the engine remap changes).
DMR_BASE = os.environ.get("DMR_HOST_URL", "http://localhost:12435")
LOCK_PATH = os.environ.get("GPU_SERIAL_LOCK", "/tmp/cu130-gpu.lock")


def _http(path: str, payload: dict | None = None, timeout: float = 10.0) -> dict | list | None:
    """GET/POST JSON against the DMR engine; None on any failure."""
    try:
        if payload is None:
            with urllib.request.urlopen(DMR_BASE + path, timeout=timeout) as r:
                return json.loads(r.read())
        req = urllib.request.Request(
            DMR_BASE + path,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            return json.loads(body) if body else {}
    except Exception:
        return None


def resident_dmr_models() -> list[str]:
    """Names of DMR models currently loaded (GET /api/ps)."""
    ps = _http("/api/ps")
    if not isinstance(ps, dict):
        return []
    return [m["name"] for m in ps.get("models", []) if m.get("name")]


def unload_dmr() -> list[str]:
    """Unload every resident DMR model; returns the names unloaded.

    Tries POST /inference/unload {"all": true} first (native runner API);
    falls back to the Ollama-compatible /api/chat with keep_alive=0 per
    model, which evicts right after serving.
    """
    loaded = resident_dmr_models()
    if not loaded:
        return []
    if _http("/inference/unload", {"all": True}) is not None:
        return loaded
    unloaded = []
    for name in loaded:
        res = _http(
            "/api/chat",
            {
                "model": name,
                "messages": [{"role": "user", "content": "."}],
                "options": {"num_predict": 1},
                "keep_alive": 0,
            },
            timeout=30.0,
        )
        if res is not None:
            unloaded.append(name)
    return unloaded


def hold_and_run(cmd: list[str]) -> int:
    """Hold the GPU flock for the whole lifetime of ``cmd``.

    The child runs as a subprocess while THIS process keeps the lock fd open.
    (The previous ``os.execvp`` version lost the lock: Python opens files with
    FD_CLOEXEC, so the locked fd was closed on exec and nothing was
    serialized.) The lock is released in ``finally`` once the child exits.
    """
    with open(LOCK_PATH, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            unloaded = unload_dmr()
            print(json.dumps({"gpu_lock": "acquired", "unloaded": unloaded}),
                  file=sys.stderr)
            try:
                return subprocess.call(cmd)
            except KeyboardInterrupt:
                return 130
            except FileNotFoundError as exc:
                print(f"gpu_serial: command not found: {exc}", file=sys.stderr)
                return 127
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


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
