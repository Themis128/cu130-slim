#!/usr/bin/env python3
"""dmr_doctor.py — diagnose the Docker Model Runner serving layer.

The failure class this fixes: /models returns 200 + lists models, but
/chat/completions hangs or disconnects (engine accepts, can't serve).

    dmr_doctor.py           # probe engines + loaded models, report serving map
    dmr_doctor.py --warm    # warm every listed model via `docker model run`
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


def _get(url: str, t: float = 6.0) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=t) as f:
            return json.loads(f.read() or b"{}")
    except Exception:
        return None


def _post_chat(base: str, model: str, t: float = 25.0) -> str:
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
    loaded = []
    for m in d.get("data", []):
        # dmr meta present = model actually loaded, not just pulled
        if m.get("dmr"):
            loaded.append(m["id"])
    return loaded


def _warm(model: str) -> str:
    r = subprocess.run(["docker", "model", "run", model, "warm"],
                       capture_output=True, text=True, timeout=180)
    return (r.stdout or r.stderr).strip()[:80]


def probe() -> dict:
    out = {}
    for base in BASES:
        name = base.split("/engines/")[1].split("/v1")[0]
        models = _models(base)
        results = {}
        for m in models[:4]:
            t0 = time.time()
            r = _post_chat(base, m)
            results[m] = f"{r} ({time.time()-t0:.1f}s)"
        out[name] = {"models": models, "serve": results}
    return out


def main() -> int:
    if "--warm" in sys.argv:
        for base in BASES:
            for m in _models(base):
                print(f"warm {m}: {_warm(m)}")
        return 0
    print(json.dumps(probe(), indent=2)[:3000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
