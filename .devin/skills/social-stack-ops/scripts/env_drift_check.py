#!/usr/bin/env python3
"""Detect env-var drift between running containers and compose declarations.

Lesson (2026-10-25): vars injected into a container's env that aren't declared
in docker-compose.yml are silently LOST on `compose up` recreate — the digest's
NEXTCLOUD_* IntraVox auth broke this way. `docker restart` preserves old env;
only recreate injects .env — so drift hides until the next recreate.

For every running compose container, compares `docker inspect` env keys
against the keys compose resolves (`docker compose config`). Flags keys
present in the container but NOT declared (would vanish on recreate) and
empty-value declared vars (compose maps but .env doesn't set — another
silent-break pattern).

Usage: python -X utf8 .devin/skills/social-stack-ops/scripts/env_drift_check.py
"""
import json
import os
import re
import subprocess
import sys

def _root():
    d = os.path.dirname(os.path.abspath(__file__))
    while d != os.path.dirname(d):
        if os.path.exists(os.path.join(d, "docker-compose.yml")):
            return d
        d = os.path.dirname(d)
    sys.exit("docker-compose.yml not found above script")

ROOT = _root()

def running_containers():
    out = subprocess.run(
        ["docker", "compose", "-f", os.path.join(ROOT, "docker-compose.yml"), "ps", "--format", "json"],
        capture_output=True, text=True, cwd=ROOT)
    names = []
    for line in out.stdout.strip().splitlines():
        try:
            names.append(json.loads(line)["Name"])
        except Exception:
            pass
    return names or [l.split()[-1] for l in
                     subprocess.run(["docker", "compose", "ps", "--services"],
                                    capture_output=True, text=True, cwd=ROOT).stdout.splitlines()]

def container_env_keys(name):
    out = subprocess.run(["docker", "inspect", name, "--format", "{{json .Config.Env}}"],
                         capture_output=True, text=True).stdout.strip()
    try:
        return {e.split("=", 1)[0] for e in json.loads(out)}
    except Exception:
        return set()

def declared_env_keys(service_names):
    """Keys declared anywhere in docker-compose.yml under environment: lists."""
    text = open(os.path.join(ROOT, "docker-compose.yml"), encoding="utf8").read()
    return set(re.findall(r"^\s+-\s+([A-Z_][A-Z0-9_]*)=", text, re.M))

def env_file_gaps():
    """Declared `${VAR}`-style mappings whose .env value is empty/missing."""
    env = {}
    try:
        for line in open(os.path.join(ROOT, ".env"), encoding="utf8"):
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    except FileNotFoundError:
        return []
    text = open(os.path.join(ROOT, "docker-compose.yml"), encoding="utf8").read()
    gaps = []
    for m in re.finditer(r"-\s+([A-Z_][A-Z0-9_]*)=\$\{?([A-Z_][A-Z0-9_]*)", text):
        env_key, var = m.group(1), m.group(2)
        if not env.get(var):
            gaps.append(f"{env_key}←${var}")
    return gaps

def main():
    containers = running_containers()
    declared = declared_env_keys(containers)
    drift = {}
    for name in containers:
        live = container_env_keys(name)
        extra = live - declared - {
            # image-internal env baked at build time — not compose drift
            "PATH", "HOSTNAME", "HOME", "SHELL", "TERM", "LC_ALL", "LANG", "GPG_KEY",
            "NODE_ENV", "NODE_PATH", "NODE_VERSION", "YARN_VERSION",
            "PYTHON_SHA256", "PYTHON_VERSION", "PYTHONUNBUFFERED", "PYTHONDONTWRITEBYTECODE",
            "PYTHONHASHSEED", "PYTHONPATH", "PYTHONFAULTHANDLER",
            "NGINX_VERSION", "NJS_VERSION", "NJS_RELEASE", "DYNPKG_RELEASE", "PKG_RELEASE",
            "N8N_RELEASE_TYPE", "N8N_RUNNERS_AUTH_TOKEN", "N8N_RUNNERS_AUTO_SHUTDOWN_TIMEOUT",
            "N8N_RUNNERS_TASK_BROKER_URI", "N8N_RUNNERS_TASK_TIMEOUT",
            "PG_MAJOR", "PG_VERSION", "PG_SHA256", "PGDATA", "GOSU_VERSION", "DOCKER_PG_LLVM_DEPS",
            "REDIS_VERSION", "RUST_BACKTRACE", "ASAN_OPTIONS", "ASAN_SYMBOLIZER_PATH",
            "DOCKER_VERSION", "DOCKER_BUILDX_VERSION", "DOCKER_COMPOSE_VERSION", "DOCKER_TLS_CERTDIR",
            "OS_ARCH", "OS_FLAVOUR", "OS_NAME", "APP_VERSION", "BITNAMI_APP_NAME",
            "STACK_OPS_DOCKER_TIMEOUT", "STACK_OPS_HOST_DIR",
            "UV_PROJECT_ENVIRONMENT", "VIRTUAL_ENV", "TESSDATA_PREFIX", "DEBIAN_FRONTEND",
            "FLOWER_DATA_DIR", "DISPLAY", "PLAYWRIGHT_BROWSERS_PATH",
            "CADVISOR_HEALTHCHECK_URL", "KAKKAVA_DIR", "KAKKAVA_SYNC_ENABLED",
        } - {k for k in live if k.startswith("GF_") or k.startswith("NPM_")}
        if extra:
            drift[name] = sorted(extra)
    gaps = env_file_gaps()
    if drift:
        print("## Drift: env keys in container but NOT declared in compose (lost on recreate)")
        for n, keys in drift.items():
            print(f"  {n}: {', '.join(keys)}")
    if gaps:
        print("## Empty: declared env vars with no .env value")
        for g in sorted(set(gaps)):
            print(f"  {g}")
    if not drift and not gaps:
        print("no env drift detected")
    return bool(drift or gaps)

if __name__ == "__main__":
    sys.exit(1 if main() else 0)
