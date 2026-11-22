#!/usr/bin/env python3
"""deploy_lag.py — is the latest CI image actually running in containers?

The SocialAuto fleet lives in compose project ``socialauto-standalone``
(deploy/socialauto-standalone/docker-compose.yml). ``stack-image-sync``
(home-ops cron */30) pulls fresh ``ghcr.io/themis128/cu130-slim:*-latest``
digests and recreates only drifted services — but merges land faster than
builds during merge storms, and recreates are cheap to misattribute. This
tool answers the question directly instead of inferring from prose:

  1. for every standalone service that declares a cu130-slim image:
     remote :latest digest  vs  local image RepoDigest  vs  the digest the
     RUNNING container was created from -> CURRENT / PULL-PENDING / LAGGING
  2. optional feature probe: ``--probe <container> <path> <regex>`` greps a
     file inside a container (e.g. prove the browser-lane latch or
     create_video_container is really in the running image, not a
     docker-cp patch that the next recreate will wipe)

Usage:
  deploy_lag.py                          # table of image drift
  deploy_lag.py --probe social-worker-meta app/services/publishing.py \\
      'create_video_container'           # exit 0 when found
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

COMPOSE_DIR = (
    Path.home() / "cu130-slim" / "deploy" / "socialauto-standalone"
)
IMAGE_RE = re.compile(r"ghcr\.io/themis128/cu130-slim:[A-Za-z0-9._-]+")


def sh(cmd: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def remote_digest(ref: str) -> str:
    r = sh(["docker", "buildx", "imagetools", "inspect", ref], timeout=60)
    m = re.search(r"Digest:\s+(sha256:[0-9a-f]+)", r.stdout)
    return m.group(1) if m else ""


def service_images() -> dict[str, str]:
    """service -> image ref, from the LIVE standalone compose."""
    import yaml

    class _Loader(yaml.SafeLoader):
        pass

    _Loader.add_multi_constructor(
        "!", lambda l, s, n: l.construct_mapping(n)
        if isinstance(n, yaml.MappingNode)
        else l.construct_sequence(n)
        if isinstance(n, yaml.SequenceNode)
        else l.construct_scalar(n),
    )
    doc = yaml.load((COMPOSE_DIR / "docker-compose.yml").read_text(), Loader=_Loader)
    out: dict[str, str] = {}
    for name, svc in (doc.get("services") or {}).items():
        svc = svc or {}
        if svc.get("profiles"):
            continue
        img = svc.get("image") or ""
        m = IMAGE_RE.search(img)
        if m:
            out[svc.get("container_name") or name] = m.group(0)
    return out


def local_repo_digest(ref: str) -> str:
    r = sh(["docker", "image", "inspect", ref,
            "--format", "{{range .RepoDigests}}{{println .}}{{end}}"], timeout=30)
    for line in r.stdout.splitlines():
        if "@" in line:
            return line.rsplit("@", 1)[1].strip()
    return ""


def container_state(container: str) -> tuple[str, str]:
    """(status, created-from image id/digest) or ('', '') when absent."""
    r = sh(["docker", "inspect", container, "--format",
            "{{.State.Status}} {{.Image}}"], timeout=20)
    parts = r.stdout.split()
    return (parts[0], parts[1]) if len(parts) == 2 else ("", "")


def image_id_digest(image_id: str) -> str:
    """RepoDigest matching a container's Image field (id or digest)."""
    r = sh(["docker", "image", "inspect", image_id, "--format",
            "{{range .RepoDigests}}{{println .}}{{end}}"], timeout=30)
    for line in r.stdout.splitlines():
        if "@" in line:
            return line.rsplit("@", 1)[1].strip()
    return ""


def probe(container: str, path: str, pattern: str) -> int:
    r = sh(["docker", "exec", container, "grep", "-c", pattern, path],
           timeout=30)
    count = (r.stdout or "").strip() or "0"
    found = r.returncode == 0 and count != "0"
    print(f"{container}: {path} ~ /{pattern}/ -> {count} match(es) "
          f"{'FOUND' if found else 'MISSING'}")
    return 0 if found else 1


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] == "--probe" and len(args) == 4:
        return probe(args[1], args[2], args[3])

    images = service_images()
    if not images:
        print("no cu130-slim images in standalone compose")
        return 1

    print(f"{'container':<34} {'state':<9} {'local':<20} {'remote':<20} verdict")
    worst = 0
    for name, ref in sorted(images.items()):
        status, img_id = container_state(name)
        remote = remote_digest(ref)
        local = local_repo_digest(ref)
        running = image_id_digest(img_id) if img_id else ""
        if not status:
            verdict = "DOWN"
        elif remote and running == remote:
            verdict = "CURRENT"
        elif remote and local == remote:
            verdict = "PULL-OK/RECREATE-PENDING"
            worst = max(worst, 1)
        else:
            verdict = "LAGGING"
            worst = max(worst, 2)
        print(f"{name:<34} {status or '-':<9} "
              f"{(running or local or '-')[7:25]:<20} "
              f"{(remote or '-')[7:25]:<20} {verdict}")
    return min(worst, 2)


if __name__ == "__main__":
    sys.exit(main())
