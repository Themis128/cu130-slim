#!/usr/bin/env python3
"""dmr_pressure.py — diagnose DMR fleet pressure before it stalls the stack.

Failure class (2026-11-13): host RAM drops below ~3GB free → DMR model
loads (CPU-pinned, weights live in system RAM) wedge in "Loading..."
state → every chat call queues on gpu:dmr_lock → 480s acquire timeouts →
publish quality gates crawl past the Celery 900s soft limit → posts
never land. /api/ps keeps listing the models with expires_at=0001
(never-unload) so the resident set looks healthy while nothing serves.

    dmr_pressure.py            # human report + verdict
    dmr_pressure.py --json     # machine-readable
    dmr_pressure.py --fix-hints # append concrete remediation commands

Checks: `docker model ps` Loading-stuck models, /api/ps resident set,
Windows free RAM (powershell interop, skipped off-WLSL), Redis
gpu:dmr_lock / gpu:media_busy, and a timed tiny-model chat probe.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import dmr_get  # noqa: E402

DOCKER_EXE = "/mnt/c/Users/tbaltzakis/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe"
REPO_ENV = Path(__file__).resolve().parents[3] / ".env"
PS_EXE = "powershell.exe"


def _sh(cmd: list[str], timeout: int = 30) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout).stdout
    except Exception:
        return ""


def docker_model_ps() -> list[dict]:
    """Parse `docker model ps` → [{name, state}] ('Loading...' = wedged)."""
    out = ""
    for docker in ([DOCKER_EXE] if Path(DOCKER_EXE).exists() else []) + ["docker"]:
        out = _sh([docker, "model", "ps"], timeout=45)
        if "MODEL NAME" in out or "Loading" in out:
            break
    rows = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 4 and parts[0] not in ("MODEL",):
            name = " ".join(parts[:-3]) if len(parts) > 4 else parts[0]
            state = " ".join(parts[-3:])
            rows.append({"name": name, "state": state})
    return rows


def resident_models() -> list[dict]:
    raw = dmr_get("/api/ps", timeout=15)
    if not raw:
        return []
    try:
        return json.loads(raw).get("models", [])
    except Exception:
        return []


def win_free_ram_gb() -> float | None:
    if not Path("/mnt/c/Windows").exists():
        return None
    out = _sh([PS_EXE, "-NoProfile", "-Command",
               "$o=Get-CimInstance Win32_OperatingSystem;"
               "[math]::Round($o.FreePhysicalMemory/1MB,1)"], timeout=30)
    m = re.search(r"[\d.]+", out)
    return float(m.group()) if m else None


def redis_gpu_state() -> dict:
    """gpu:dmr_lock value+TTL and gpu:media_busy via the redis container."""
    state = {}
    url = _sh(["docker", "exec", "social-worker-publishing",
               "printenv", "REDIS_URL"], timeout=20).strip()
    pw = ""
    m = re.search(r"://:?([^@]*)@", url)
    if m:
        pw = m.group(1)
    for key in ("gpu:dmr_lock", "gpu:media_busy"):
        cmd = ["docker", "exec", "redis", "redis-cli", "-a", pw,
               "--no-auth-warning"]
        if key == "gpu:dmr_lock":
            val = _sh(cmd + ["GET", key], timeout=15).strip()
            ttl = _sh(cmd + ["TTL", key], timeout=15).strip()
            state["dmr_lock"] = {"holder": val or None,
                                 "ttl_s": int(ttl) if ttl.lstrip("-").isdigit() else None}
        else:
            state["media_busy"] = bool(_sh(cmd + ["GET", key],
                                           timeout=15).strip())
    return state


def chat_probe(timeout: int = 60) -> dict:
    """Timed tiny-model probe — the canary for 'loads wedged'."""
    body = json.dumps({"model": "ai/smollm3",
                       "messages": [{"role": "user", "content": "ok"}],
                       "max_tokens": 3}).encode()
    req = urllib.request.Request(
        "http://localhost:12435/engines/llama.cpp/v1/chat/completions",
        data=body, headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            json.loads(r.read())
        return {"ok": True, "secs": round(time.time() - t0, 1)}
    except Exception as exc:
        # container fallback like _common.dmr_post
        payload = json.dumps({"method": "POST",
                              "url": "http://host.docker.internal:12435"
                                     "/engines/llama.cpp/v1/chat/completions",
                              "body": json.loads(body), "timeout": timeout}).encode()
        p = subprocess.run(["docker", "exec", "-i", "social-api", "python",
                            "-c", _PY_SNIPPET], input=payload,
                           capture_output=True, timeout=timeout + 20)
        ok = p.returncode == 0 and p.stdout.strip()
        return {"ok": bool(ok), "secs": round(time.time() - t0, 1),
                "err": None if ok else (p.stderr.decode()[:120] or "timeout")}


_PY_SNIPPET = (
    "import json,sys,urllib.request\n"
    "req=json.load(sys.stdin)\n"
    "r=urllib.request.Request(req['url'],data=json.dumps(req['body']).encode(),"
    "headers={'Content-Type':'application/json'},method=req['method'])\n"
    "print(urllib.request.urlopen(r,timeout=req.get('timeout',30)).read().decode())\n"
)


def verdict(report: dict) -> tuple[str, list[str]]:
    hints = []
    loading = [m for m in report["model_ps"] if "Loading" in m["state"]]
    ram = report.get("win_free_ram_gb")
    findings = []

    if loading:
        findings.append(f"{len(loading)} model(s) stuck Loading: "
                        + ", ".join(m["name"] for m in loading[:4]))
        hints += [
            "docker model restart-runner   # clears wedged loads (models reload on demand)",
            "dmr-unload.py --all           # or unload non-warm models to free RAM",
        ]
    if ram is not None and ram < 4.0:
        findings.append(f"Windows free RAM {ram}GB < 4GB — model loads cannot fit")
        hints += [
            "close memory hogs on Windows (vmmemWSL cap is .wslconfig memory=)",
            "durable: wsl --shutdown + Docker Desktop restart (kills IDE/WSL sessions)",
        ]
    lock = (report.get("redis") or {}).get("dmr_lock") or {}
    if lock.get("ttl_s") and lock["ttl_s"] > 300:
        findings.append(f"gpu:dmr_lock held ~{lock['ttl_s']}s — callers eat 480s timeouts")
        hints.append("if holder is wedged: redis-cli DEL gpu:dmr_lock (TTL self-heals at 660s)")
    if report.get("chat_probe", {}).get("ok") is False:
        findings.append("tiny-model chat probe FAILED — DMR not serving")
        hints.append("check `docker model ps` + dmr-watchdog logs; restart-runner if wedged")

    if not findings:
        return "OK", ["fleet healthy — no pressure signature"]
    sev = "CRITICAL" if (loading and (ram or 99) < 4) or report.get("chat_probe", {}).get("ok") is False else "WARN"
    return sev, findings + hints


def main() -> int:
    report = {
        "model_ps": docker_model_ps(),
        "resident": resident_models(),
        "win_free_ram_gb": win_free_ram_gb(),
        "redis": redis_gpu_state(),
        "chat_probe": chat_probe(),
    }
    sev, lines = verdict(report)
    report["verdict"] = sev
    report["findings"] = lines

    if "--json" in sys.argv:
        print(json.dumps(report, indent=2))
    else:
        print(f"DMR pressure: {sev}")
        for line in lines:
            print(f"  - {line}")
        res = report["resident"]
        if res:
            print(f"resident models: {len(res)}")
            for m in res:
                print(f"  {m.get('name','?')}  expires={m.get('expires_at','?')}")
    return 0 if sev == "OK" else 1


if __name__ == "__main__":
    sys.exit(main())
