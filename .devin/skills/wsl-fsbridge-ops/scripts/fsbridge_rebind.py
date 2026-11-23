#!/usr/bin/env python3
"""fsbridge_rebind.py — auto-heal Docker Desktop fsbridge mount poisoning.

Failure class: after a vsock flap the hashed bind-mount dirs under
/mnt/wsl/docker-desktop-bind-mounts/Ubuntu-24.04/<sha256(src)> materialize
EMPTY inside containers -> mass Exited(127). The dd-user-distro-proxy
journal records every "mounting <src> to .../<hash>" translation, which is
the authoritative hash->source map; sha256(source) cross-checks it.

Action: for every empty hash that a live/dead container references, rebind
it inside the docker-desktop VM to a FRESH /dev/sdd mount of the Ubuntu
disk (verified procedure 2026-11-20/23), then `docker start` the Exited(127)
casualties. Orphan empty hashes (no container refs) are lazily unmounted
and removed.

Probe + rebind both run INSIDE the docker-desktop VM via a privileged
container + `nsenter -t 1` (pure docker CLI — no Windows interop needed,
which matters because fsbridge poison coincides with vsock flaps that kill
wsl.exe/cmd.exe interop). The dd-user-distro-proxy journal records every
"mounting <src> to .../<hash>" translation = authoritative hash->source map;
sha256(source) cross-checks it; mappings persist in state.json.
Throttle: ~/.local/state/fsbridge-rebind/state.json — min 10 min between
rebind attempts so a non-sticking heal alerts instead of looping.

Log: ~/logs/fsbridge-rebind.log
Alert: tg_alert.py on heal / unmapped-hash / failure (throttled 30 min).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

BM = Path("/mnt/wsl/docker-desktop-bind-mounts/Ubuntu-24.04")
DISTRO = "Ubuntu-24.04"
STATE_DIR = Path.home() / ".local/state/fsbridge-rebind"
STATE = STATE_DIR / "state.json"
LOG = Path.home() / "logs/fsbridge-rebind.log"
TG = "/home/tbaltzakis/cu130-slim/scripts/tg_alert.py"
MIN_INTERVAL = 600  # seconds between rebind attempts
VM_PREFIX = "/mnt/host/wsl/docker-desktop-bind-mounts/Ubuntu-24.04"

# Rebind executes INSIDE the docker-desktop VM via a privileged container +
# nsenter into init's mount namespace — pure docker CLI, no Windows interop
# needed (works even when wsl.exe/cmd.exe interop is dead, which is exactly
# when fsbridge poison coincides with vsock flaps).
VM_IMAGE = os.environ.get(
    "FSBRIDGE_VM_IMAGE", "ghcr.io/themis128/cu130-slim:social-backend-latest"
)


def log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}"
    with LOG.open("a") as f:
        f.write(line + "\n")
    print(line)


def alert(key: str, msg: str) -> None:
    """Throttled Telegram alert (30 min per key)."""
    state = _load_state()
    alerts = state.setdefault("alerts", {})
    now = time.time()
    if now - alerts.get(key, 0) < 1800:
        return
    try:
        subprocess.run(
            ["python3", TG, "P2", msg],
            capture_output=True, timeout=30,
        )
        alerts[key] = now
        _save_state(state)
    except Exception as exc:  # noqa: BLE001
        log(f"alert send failed: {exc}")


def _load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:  # noqa: BLE001
        return {}


def _save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state))


def docker(*args: str, timeout: int = 30) -> str:
    r = subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=timeout
    )
    return r.stdout


VM_PROBE = r"""
BM=/mnt/host/wsl/docker-desktop-bind-mounts/Ubuntu-24.04
[ -d "$BM" ] || exit 0
for h in "$BM"/*; do
  n=${h##*/}
  if [ -d "$h" ]; then
    [ -z "$(ls -A "$h" 2>/dev/null)" ] && echo "EMPTY $n"
  elif [ -f "$h" ] && [ ! -s "$h" ]; then
    echo "ZERO $n"
  fi
done
"""


def vm_probe() -> tuple[set[str], set[str]] | None:
    """Authoritative empty/zero-byte hash probe INSIDE the docker-desktop VM
    (root sees everything the Ubuntu-side 9p view hides behind perms).
    Returns (empty_dirs, zero_files) or None if the VM path is unreachable."""
    import base64

    b64 = base64.b64encode(VM_PROBE.encode()).decode()
    try:
        r = subprocess.run(
            [
                "docker", "run", "--rm",
                "--privileged", "--pid=host", "--user", "root",
                VM_IMAGE,
                "nsenter", "-t", "1", "-m", "-u", "-i", "-n",
                "sh", "-c", f"echo {b64} | base64 -d | sh",
            ],
            capture_output=True, text=True, timeout=120,
        )
    except Exception as exc:  # noqa: BLE001
        log(f"vm probe failed: {exc}")
        return None
    if r.returncode != 0:
        log(f"vm probe rc={r.returncode}: {(r.stderr or r.stdout)[-200:]}")
        return None
    empty, zero = set(), set()
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == "EMPTY":
            empty.add(parts[1])
        elif len(parts) == 2 and parts[0] == "ZERO":
            zero.add(parts[1])
    return empty, zero


def empty_hashes() -> tuple[set[str], set[str]]:
    """Ubuntu-side fallback probe (used only if the VM probe is unreachable)."""
    empty: set[str] = set()
    zero_files: set[str] = set()
    if not BM.is_dir():
        return empty, zero_files
    for h in BM.iterdir():
        try:
            if h.is_dir():
                if not any(h.iterdir()):
                    empty.add(h.name)
            elif h.is_file() and h.stat().st_size == 0:
                zero_files.add(h.name)
        except PermissionError:
            continue
    return empty, zero_files


def container_bind_sources() -> dict[str, str]:
    """hash-or-source -> set of referencing container names (approx via mounts)."""
    ids = docker("ps", "-aq").split()
    refs: dict[str, set[str]] = {}
    for cid in ids:
        name = docker("inspect", "-f", "{{.Name}}", cid).strip().lstrip("/")
        mounts = docker("inspect", "-f", "{{json .Mounts}}", cid)
        try:
            for m in json.loads(mounts):
                if m.get("Type") != "bind":
                    continue
                src = m.get("Source", "")
                # hashed engine-side source -> inner hash is the mount dir
                if f"docker-desktop-bind-mounts/{DISTRO}/" in src:
                    inner = src.rstrip("/").rsplit("/", 1)[-1]
                    refs.setdefault(inner, set()).add(name)
                else:
                    h = hashlib.sha256(src.encode()).hexdigest()
                    refs.setdefault(h, set()).add(name)
        except Exception:  # noqa: BLE001
            continue
    return refs


def journal_hash_map() -> dict[str, str]:
    """dd-user-distro-proxy logs 'mounting <src> to .../<hash>' — authoritative."""
    out = {}
    try:
        r = subprocess.run(
            ["journalctl", "-u", "dd-user-distro-proxy.service", "--no-pager", "-o", "cat"],
            capture_output=True, text=True, timeout=30,
        )
        pat = re.compile(
            r"mounting (\S+) to /mnt/wsl/docker-desktop-bind-mounts/"
            + DISTRO
            + r"/([a-f0-9]{64})"
        )
        for line in r.stdout.splitlines():
            m = pat.search(line)
            if m:
                out[m.group(2)] = m.group(1)
    except Exception as exc:  # noqa: BLE001
        log(f"journal map failed: {exc}")
    return out


def sha_map() -> dict[str, str]:
    """sha256(source) for every bind source currently known to docker."""
    out = {}
    ids = docker("ps", "-aq").split()
    for cid in ids:
        mounts = docker("inspect", "-f", "{{json .Mounts}}", cid)
        try:
            for m in json.loads(mounts):
                src = m.get("Source", "")
                if m.get("Type") == "bind" and src and "docker-desktop-bind-mounts" not in src:
                    out[hashlib.sha256(src.encode()).hexdigest()] = src
        except Exception:  # noqa: BLE001
            continue
    return out


def vm_source(src: str) -> str | None:
    """Map a WSL/Windows source path to its path inside the docker-desktop VM."""
    if src == "/run/docker.sock":
        return "/run/guest-services/docker.container-proxy.sock"
    if src.startswith("/mnt/c/") or src.startswith("C:\\") or src.startswith("C:/"):
        # windows path -> /mnt/host/c/...
        p = src.replace("C:\\", "/mnt/host/c/").replace("C:/", "/mnt/host/c/")
        if src.startswith("/mnt/c/"):
            p = "/mnt/host/c/" + src[len("/mnt/c/"):]
        return p
    # ubuntu disk content lives under the fresh /dev/sdd mount
    return "/tmp/ubnt" + src


def build_vm_script(
    plan: list[tuple[str, str, str]], orphans: list[str]
) -> str:
    """plan = [(hash, vm_src_path, 'dir'|'file'|'sock')]; orphans = empty
    hashes no container references (lazy umount+rmdir)."""
    lines = [
        "#!/bin/sh",
        "set -u",
        "BM=/mnt/host/wsl/docker-desktop-bind-mounts/Ubuntu-24.04",
        "mkdir -p /tmp/ubnt",
        "mountpoint -q /tmp/ubnt || mount /dev/sdd /tmp/ubnt || { echo FRESH_MOUNT_FAILED; exit 1; }",
    ]
    for h, src, kind in plan:
        target = f"$BM/{h}"
        tag = kind[:4]
        if kind == "dir":
            body = [
                f'umount -l "{target}" 2>/dev/null',
                f'mount --bind "{src}" "{target}" && echo "OK {tag} {h[:12]}" || echo "FAIL {tag} {h[:12]}"',
            ]
            # authoritative empty-source guard: /tmp/ubnt sees root-only
            # dirs the Ubuntu-side probe can't list (e.g. /var/lib/docker)
            guard = f'[ -d "{src}" ] && [ -z "$(ls -A "{src}" 2>/dev/null)" ]'
            lines += [
                f"if {guard}; then echo \"SKIP_EMPTY_SRC {h[:12]}\"; else",
                *[f"  {c}" for c in body],
                "fi",
            ]
        else:  # file | sock — target must be a plain file before bind
            lines += [
                f'umount -l "{target}" 2>/dev/null',
                f'rm -rf "{target}"; touch "{target}"',
                f'mount --bind "{src}" "{target}" && echo "OK {tag} {h[:12]}" || echo "FAIL {tag} {h[:12]}"',
            ]
    # orphan empty hashes: lazy-unmount + rmdir (zero container refs only)
    lines.append('echo "=== orphans ==="')
    for h in orphans:
        lines.append(f'umount -l "$BM/{h}" 2>/dev/null; rmdir "$BM/{h}" 2>/dev/null && echo "ORPHAN_RM {h[:12]}"')
    return "\n".join(lines) + "\n"


def run_in_vm(script: str) -> tuple[bool, str]:
    """Execute the rebind script inside the docker-desktop VM.

    privileged+pid=host container -> nsenter into PID1's namespaces gives
    the VM's mount namespace. Script is piped via base64 (no file sharing
    needed). Requires the backend image locally (always present)."""
    import base64

    b64 = base64.b64encode(script.encode()).decode()
    try:
        r = subprocess.run(
            [
                "docker", "run", "--rm",
                "--privileged", "--pid=host", "--user", "root",
                VM_IMAGE,
                "nsenter", "-t", "1", "-m", "-u", "-i", "-n",
                "sh", "-c", f"echo {b64} | base64 -d | sh",
            ],
            capture_output=True, text=True, timeout=300,
        )
        return r.returncode == 0, (r.stdout + r.stderr)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def source_kind(src: str) -> str:
    if src == "/run/docker.sock":
        return "sock"
    # /mnt/c sources: check on the windows side via /mnt/c
    check = src
    if src.startswith("/mnt/c/"):
        check = src  # visible directly in WSL
    elif src.startswith("C:\\") or src.startswith("C:/"):
        p = src.replace("C:\\", "/mnt/c/").replace("C:/", "/mnt/c/")
        check = p
    else:
        check = src
    try:
        if os.path.isfile(check):
            return "file"
        if os.path.isdir(check):
            return "dir"
    except OSError:
        pass
    # unknown — default dir (mount --bind of missing src fails loudly in VM log)
    return "dir"


def main() -> int:
    if not BM.is_dir():
        log("bind-mounts dir absent — fsbridge namespace not mounted; skip")
        return 0
    try:
        docker("info", timeout=15)
    except Exception:
        log("docker engine unreachable — skip")
        return 0

    probed = vm_probe()
    if probed is None:
        empty_dirs, zero_files = empty_hashes()
    else:
        empty_dirs, zero_files = probed
    suspects = set(empty_dirs) | set(zero_files)
    if not suspects:
        return 0  # healthy — cheap exit

    state = _load_state()
    now = time.time()
    last = state.get("last_rebind", 0)
    if now - last < MIN_INTERVAL:
        # still poisoned right after a heal attempt -> alert once, don't loop
        still = sorted(suspects)
        alert("rebind-stuck", f"fsbridge still poisoned {int(now-last)}s after rebind — {len(still)} suspect mounts: {', '.join(h[:8] for h in still)}")
        return 0

    refs = container_bind_sources()
    journal = journal_hash_map()
    shas = sha_map()

    # learned map persisted across runs
    learned = state.setdefault("hashmap", {})
    for h, s in journal.items():
        learned[h] = s
    for h, s in shas.items():
        learned.setdefault(h, s)

    plan: list[tuple[str, str, str]] = []
    unmapped: list[str] = []
    skipped_empty_src: list[str] = []
    orphans: list[str] = []
    for h in sorted(suspects):
        src = learned.get(h)
        if not src:
            unmapped.append(h)
            continue
        # orphan hash: no container references it -> empty is harmless,
        # clean it up instead of rebinding
        if h not in refs:
            orphans.append(h)
            continue
        # source dir that is itself empty -> hash being empty is NOT poison
        if source_kind(src) == "dir":
            try:
                if os.path.isdir(src) and not os.listdir(src):
                    skipped_empty_src.append(h)
                    continue
            except OSError:
                pass  # unreadable (root-only) — VM-side guard decides
        vm_src = vm_source(src)
        if vm_src is None:
            unmapped.append(h)
            continue
        plan.append((h, vm_src, source_kind(src)))

    if unmapped:
        log(f"unmapped empty hashes: {[h[:12] for h in unmapped]} — cannot rebind safely")
        alert("unmapped", f"fsbridge: {len(unmapped)} empty mounts have no known source — manual rebind needed: {', '.join(h[:12] for h in unmapped)}")

    if skipped_empty_src:
        log(f"skipped {len(skipped_empty_src)} hashes whose source dir is itself empty (not poison)")
    if orphans:
        log(f"{len(orphans)} orphan empty hashes (no container refs) — will clean")

    if not plan and not orphans:
        log(f"{len(suspects)} suspect mounts, none actionable — nothing to do")
        return 0

    if plan:
        log(f"poison detected: {len(suspects)} suspect mounts, rebinding {len(plan)}")
    script = build_vm_script(plan, orphans)
    ok, out = run_in_vm(script)
    log("vm output: " + " | ".join(l for l in out.splitlines() if l.startswith(("OK", "FAIL", "FRESH", "SKIP", "ORPHAN"))))

    if not ok:
        state["last_rebind"] = now
        _save_state(state)
        alert("rebind-fail", f"fsbridge auto-rebind failed: {out[-300:]}")
        return 1

    fails = [l for l in out.splitlines() if l.startswith("FAIL")]
    if fails:
        state["last_rebind"] = now
        _save_state(state)
        alert("rebind-partial", f"fsbridge rebind partial: {fails}")
        return 1

    # restart casualties
    casualties = []
    for cid in docker("ps", "-aq", "--filter", "status=exited").split():
        if docker("inspect", "-f", "{{.State.ExitCode}}", cid).strip() == "127":
            casualties.append(cid)
    if casualties:
        names = docker("inspect", "-f", "{{.Name}}", *casualties).split()
        r = subprocess.run(["docker", "start", *casualties], capture_output=True, text=True, timeout=120)
        log(f"restarted {len(casualties)} casualties: {r.stdout.strip()[:200]} {r.stderr.strip()[:200]}")

    ok_n = sum(1 for l in out.splitlines() if l.startswith("OK"))
    skip_n = sum(1 for l in out.splitlines() if l.startswith("SKIP"))
    real_binds = ok_n > 0 or any(l.startswith("FAIL") for l in out.splitlines())
    # throttle only on real rebind activity — skip/orphan-only passes must
    # not delay the next genuine poison wave
    if real_binds:
        state["last_rebind"] = now
        _save_state(state)
    log(f"heal complete: {ok_n} mounts rebound, {skip_n} skipped (empty source), {len(casualties)} containers restarted")
    if ok_n or orphans:
        alert("healed", f"fsbridge auto-heal: {ok_n} mounts rebound, {len(orphans)} orphans cleaned, {len(casualties)} containers restarted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
