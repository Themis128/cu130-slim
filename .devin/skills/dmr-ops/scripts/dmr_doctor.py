#!/usr/bin/env python3
"""dmr_doctor.py — diagnose the Docker Model Runner serving layer.

The failure class this fixes: /models returns 200 + lists models, but
/chat/completions hangs or disconnects (engine accepts, can't serve).

    dmr_doctor.py           # probe engines + loaded models, report serving map
    dmr_doctor.py --warm    # warm every listed model via `docker model run`

Cold-load policy (per docker-model-runner skill): a chat call on a
non-resident model triggers a full load — 60–290s documented. Resident
models get a 25s probe budget
non-resident get 300s. Probes that would
trigger a load are skipped entirely while a GPU arbiter lock
(``gpu:media_lock`` / ``gpu:dmr_lock``) is held — uncoordinated loads
during a media job can wedge the runner.
"""
import json
import subprocess
import sys
import time
import urllib.request

BASES = [
    "http://localhost:12435/engines/llama.cpp/v1",
    "http://localhost:12435/engines/vllm/v1",
]
PROMPT = [{"role": "user", "content": "reply with ok"}]
DMR_PS_URL = "http://localhost:12435/api/ps"
WARM_TIMEOUT_S = 25.0
COLD_TIMEOUT_S = 300.0


def _get(url: str, t: float = 6.0) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=t) as f:
            return json.loads(f.read() or b"{}")
    except Exception:
        return None


def _post_chat(base: str, model: str, t: float) -> str:
    body = json.dumps({"model": model, "messages": PROMPT,
                       "max_tokens": 5}).encode()
    r = urllib.request.Request(f"{base}/chat/completions", data=body,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=t) as f:
            d = json.loads(f.read())
            ch = (d.get("choices") or [{}])[0]
            return ch.get("message", {}).get("content", "?")[:60]
    except Exception as e:
        return f"FAIL {type(e).__name__}"


def _models(base: str) -> list[str]:
    d = _get(f"{base}/models")
    if not d:
        return []
    return [m["id"] for m in d.get("data", []) if m.get("id")]


def _resident() -> set[str]:
    """Models currently loaded in the runner (/api/ps), normalized."""
    d = _get(DMR_PS_URL)
    return {
        str(m.get("name", "")).lower().replace("hf.co/", "huggingface.co/")
        for m in (d or {}).get("models") or []
    }


def _is_resident(model: str, running: set[str]) -> bool:
    norm = model.lower().replace("hf.co/", "huggingface.co/")
    return any(norm in r for r in running)


def _gpu_busy() -> bool:
    """True while the GPU arbiter's media or DMR lock is held in Redis."""
    for key in ("gpu:media_lock", "gpu:dmr_lock"):
        try:
            r = subprocess.run(
                ["docker", "exec", "-i", "social-redis",
                 "redis-cli", "exists", key],
                capture_output=True, text=True, timeout=15)
            if r.stdout.strip() == "1":
                return True
        except Exception:
            continue
    return False


def _warm(model: str) -> str:
    try:
        r = subprocess.run(["docker", "model", "run", model, "warm"],
                           capture_output=True, text=True, timeout=180)
        return (r.stdout or r.stderr).strip()[:80]
    except subprocess.TimeoutExpired:
        return "TIMEOUT (load still running — >180s)"
    except Exception as e:
        return f"ERR {type(e).__name__}: {e}"


def probe() -> dict:
    out = {}
    gpu_blocked = _gpu_busy()
    running = _resident()
    for base in BASES:
        name = base.split("/engines/")[1].split("/v1")[0]
        models = _models(base)
        results = {}
        for m in models:
            resident = _is_resident(m, running)
            if not resident and gpu_blocked:
                results[m] = "SKIP gpu-busy (probe would trigger an uncoordinated load)"
                continue
            t0 = time.time()
            r = _post_chat(base, m, t=WARM_TIMEOUT_S if resident else COLD_TIMEOUT_S)
            tag = "" if resident else " [cold]"
            results[m] = f"{r} ({time.time()-t0:.1f}s){tag}"
        out[name] = {"models": models, "serve": results}
    if gpu_blocked:
        out["gpu_note"] = "a GPU arbiter lock is held — cold-model probes skipped"
    return out


def main() -> int:
    if "--warm" in sys.argv:
        if _gpu_busy():
            print("gpu busy (arbiter lock held) — refusing to warm; retry when idle")
            return 1
        for base in BASES:
            for m in _models(base):
                print(f"warm {m}: {_warm(m)}")
        return 0
    print(json.dumps(probe(), indent=2)[:3000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
