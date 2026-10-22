#!/usr/bin/env python3
"""gpu-runner-recreate.py — restore/self-heal the GPU Docker Model Runner.

The `docker-model-runner` container serves the app on host port 12435:
  - llama.cpp (CUDA, -ngl 999 full GPU offload) for GGUF models — production
  - vLLM for safetensors models — experimental

It normally has RestartPolicy=always and persists models in the
`docker-model-runner-models` named volume. This script handles the cases
that policy misses:
  1. Container exists but stopped/wedged  -> start (or restart if engine dead)
  2. Container missing (Desktop reset)    -> recreate from the fixed image

The fixed image `local/model-runner:vllm-cuda-fixed` is a commit of the
stock `docker/model-runner:latest-vllm-cuda` with torch installed into
/opt/vllm-env (the stock image ships a broken +cpu torch).

Usage:
  gpu-runner-recreate.py          # heal or recreate
  gpu-runner-recreate.py --force  # always recreate the container

After recreation, re-apply per-model vLLM memory config:
  docker model configure --gpu-memory-utilization 0.7 ai/smollm2-vllm"""

import subprocess
import sys
import time
import urllib.request
import json
from pathlib import Path

NAME = "docker-model-runner"
IMAGE = "local/model-runner:vllm-cuda-fixed"
PORT = "12435"
MODELS_VOLUME = "docker-model-runner-models"
HEALTH_URL = f"http://localhost:{PORT}/engines/v1/models"
DMR_BASE = f"http://localhost:{PORT}"


def find_wsl_lib_src() -> Path | None:
    """Auto-detect WSL libcuda.so.1.1 mount path (works across Docker Desktop versions)."""
    # Strategy 1: Docker Desktop bind-mount path (version-specific)
    base = Path("/run/desktop/mnt/host/wsl/docker-desktop-bind-mounts")
    if base.exists():
        for distro in base.iterdir():
            candidate = distro / "eead6ba894879fdc294fc12b89c87a9375c56ccfe49d00db241cbfecbdbf5a1a"
            if candidate.is_dir() and (candidate / "libcuda.so.1.1").exists():
                return candidate
    # Strategy 2: Standard WSL driver path
    wsl_drivers = Path("/usr/lib/wsl/drivers")
    if wsl_drivers.exists():
        for d in wsl_drivers.glob("nvltsi.inf_amd64_*"):
            if (d / "libcuda.so.1.1").exists():
                return d
    # Strategy 3: ldconfig fallback
    try:
        import subprocess
        result = subprocess.run(["ldconfig", "-p"], capture_output=True, text=True, timeout=5)
        for line in result.stdout.splitlines():
            if "libcuda.so.1" in line:
                parts = line.split()
                if len(parts) >= 4:
                    path = Path(parts[-1]).parent
                    if path.exists() and (path / "libcuda.so.1.1").exists():
                        return path
    except Exception:
        pass
    return None


def log(msg: str) -> None:
    print(f"[gpu-runner] {msg}")


def healthy() -> bool:
    try:
        urllib.request.urlopen(HEALTH_URL, timeout=5)
        return True
    except Exception:
        return False


def warmup_models() -> None:
    """Issue warm-up requests for primary models to avoid cold-start latency."""
    # Only pre-warm the 30m keep_alive-pinned 4B. Loading the 8B too (~5.1GB
    # + ~2.7GB) can exceed the shared 8GB card and leaves both resident,
    # fighting media GPU arbitration — the 8B lazy-loads on demand instead.
    primary_models = [
        "hf.co/unsloth/Qwen3-4B-Instruct-2507-GGUF:Q4_K_M",
    ]
    for model in primary_models:
        try:
            req = urllib.request.Request(
                f"{DMR_BASE}/engines/v1/chat/completions",
                data=json.dumps({
                    "model": model,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 1,
                    "temperature": 0,
                }).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=30)
            log(f"warmed up {model}")
        except Exception as e:
            log(f"warmup failed for {model}: {e}")


if "--force" in sys.argv[1:]:
    log("--force: removing existing container")
    subprocess.run(["docker", "rm", "-f", NAME], capture_output=True)

if subprocess.run(["docker", "inspect", NAME],
                  capture_output=True).returncode == 0:
    if healthy():
        log(f"runner is up and healthy on :{PORT} — nothing to do")
        sys.exit(0)
    log("container exists but engine unhealthy — restarting")
    subprocess.run(["docker", "restart", NAME], check=True)
    time.sleep(5)
    if healthy():
        log("runner recovered after restart")
        sys.exit(0)
    log("restart did not recover — recreating container")
    subprocess.run(["docker", "rm", "-f", NAME], capture_output=True)

if subprocess.run(["docker", "image", "inspect", IMAGE],
                  capture_output=True).returncode:
    log(f"ERROR: {IMAGE} not found locally.")
    log("Rebuild it: docker model install-runner --backend vllm "
        f"--gpu cuda --port {PORT} \\")
    log("  then fix torch inside the container:")
    log("    /home/modelrunner/.local/bin/uv pip install "
        "--python /opt/vllm-env/bin/python \\")
    log("      --index-url https://download.pytorch.org/whl/cu126 "
        "--force-reinstall 'torch==2.13.0'")
    log(f"  and commit: docker commit <container> {IMAGE}")
    sys.exit(1)

wsl_lib = find_wsl_lib_src()
mount_args = []
if wsl_lib:
    mount_args = ["-v", f"{wsl_lib}:/usr/lib/wsl/lib:ro"]
    log(f"using WSL lib mount: {wsl_lib}")
else:
    log("WARNING: WSL libcuda.so.1.1 not found — GPU may not initialize")

log(f"creating {NAME} from {IMAGE} on 127.0.0.1:{PORT}")
subprocess.run([
    "docker", "run", "-d", "--name", NAME, "--restart", "always",
    "--gpus", "all", "-p", f"127.0.0.1:{PORT}:12435",
    "-v", f"{MODELS_VOLUME}:/models:z", *mount_args,
    "-e", "MODEL_RUNNER_PORT=12435",
    "-e", "MODEL_RUNNER_ENVIRONMENT=moby",
    "-e", "MODEL_RUNNER_SOCK=/var/run/model-runner/model-runner.sock",
    "-e", "MODELS_PATH=/models",
    "-e", "LLAMA_ARG_HOST=0.0.0.0",
    "-e", "VLLM_WSL2_ENABLE_PIN_MEMORY=1",
    "-e", "VLLM_USE_FLASHINFER_SAMPLER=0",
    IMAGE], check=True)

log("waiting for engine health...")
for i in range(24):
    if healthy():
        log(f"runner healthy on :{PORT} ({i}0s)")
        warmup_models()
        sys.exit(0)
    time.sleep(10)

log(f"ERROR: runner did not become healthy within 240s — "
    f"check: docker logs {NAME}")
sys.exit(1)
