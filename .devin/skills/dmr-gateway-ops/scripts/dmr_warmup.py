#!/usr/bin/env python3
"""dmr_warmup.py — provisioned-throughput adopt for the DMR.
Keeps the gateway's routed models hot: primes them on start + pings them on a
schedule so the DMR doesn't cold-unload between calls (the minutes-long load)."""
import json
import subprocess
import sys
import time
import urllib.request

DMR = "http://localhost:12435/engines/v1/chat/completions"
MODELS = ["ai/llama3.2", "docker.io/ai/qwen3-embedding:latest"]
INTERVAL = 240  # ping every 4min — under the DMR's unload window


def _warm(model):
    if "embed" in model:
        body = json.dumps({"model": model, "input": "warm"}).encode()
        url = DMR.replace("chat/completions", "embeddings")
    else:
        body = json.dumps({"model": model, "messages": [
            {"role": "user", "content": "ok"}], "max_tokens": 3}).encode()
        url = DMR
    try:
        urllib.request.urlopen(urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"}), timeout=300)
        return True
    except Exception:
        return False


def prime():
    return {m: _warm(m) for m in MODELS}


def keepalive():
    while True:
        for m in MODELS:
            _warm(m)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "prime"
    if cmd == "keepalive":
        keepalive()
    else:
        print(json.dumps(prime(), indent=1))
