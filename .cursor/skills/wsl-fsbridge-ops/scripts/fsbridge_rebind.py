#!/usr/bin/env python3
"""fsbridge_rebind.py — auto-heal Docker Desktop fsbridge mount poisoning.

Failure class: after a vsock flap the hashed bind-mount dirs under
/mnt/wsl/docker-desktop-bind-mounts/Ubuntu-24.04/<sha256(src)> materialize
EMPTY inside containers -> mass Exited(127). The dd-user-distro-proxy
journal records every "mounting <src> to .../<hash>" translation, which is
the authoritative hash->source map; sha256(source) cross-checks it.

Action: for every suspect hash that a live/dead container references, rebind
it inside the docker-desktop VM to a FRESH /dev/sdd mount of the Ubuntu
disk (verified procedure 2026-11-20/23/24). Suspect = empty dir, zero-byte
file, WRONG SHAPE (dir where the source is a file/sock — "mount a directory
onto a file" on start, the 2026-11-24 wave the empty/zero-only probe
missed), file where a dir is expected, or missing node. Outer double-hash
mount dirs (source recorded as another bind-mounts VM path — clickhouse)
are learned and rebound via their inner-hash chain. Then recover the
Exited(127)/created casualties — compose-managed ones via
`up -d --force-recreate --no-deps` (restart preserves the poisoned mount
table; recreate re-materializes it — verified 2026-11-24), non-compose via
`docker start`. Orphan empty hashes (no container refs) are lazily
unmounted and removed.

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
  echo "$n" | grep -qE '^[0-9a-f]{64}$' || continue
  if [ -d "$h" ]; then
    echo "SHAPE $n d"
    [ -z "$(ls -A "$h" 2>/dev/null)" ] && echo "EMPTY $n"
  elif [ -f "$h" ]; then
    echo "SHAPE $n f"
    [ ! -s "$h" ] && echo "ZERO $n"
  else
    echo "SHAPE $n o"
  fi
done
exit 0
"""


def vm_probe() -> tuple[set[str], set[str], dict[str, str]] | None:
    """Authoritative hash probe INSIDE the docker-desktop VM (root sees
    everything the Ubuntu-side 9p view hides behind perms).
    Returns (empty_dirs, zero_files, shapes) where shapes maps
    hash -> 'd'|'f'|'o' (dir/file/other) for EVERY hash — needed to catch
    the wrong-shape poison class (a directory materializing where the
    source is a file/sock, which produces 'mount a directory onto a file'
    on container start and was invisible to the empty/zero probe —
    2026-11-24 wave). Returns None if the VM path is unreachable."""
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
    empty, zero, shapes = set(), set(), {}
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == "EMPTY":
            empty.add(parts[1])
        elif len(parts) == 2 and parts[0] == "ZERO":
            zero.add(parts[1])
        elif len(parts) == 3 and parts[0] == "SHAPE":
            shapes[parts[1]] = parts[2]
    return empty, zero, shapes


def empty_hashes() -> tuple[set[str], set[str], dict[str, str]]:
    """Ubuntu-side fallback probe (used only if the VM probe is unreachable).
    Returns (empty_dirs, zero_files, shapes) like vm_probe()."""
    empty: set[str] = set()
    zero_files: set[str] = set()
    shapes: dict[str, str] = {}
    if not BM.is_dir():
        return empty, zero_files, shapes
    for h in BM.iterdir():
        if not re.fullmatch(r"[0-9a-f]{64}", h.name):
            continue
        try:
            if h.is_dir():
                shapes[h.name] = "d"
                if not any(h.iterdir()):
                    empty.add(h.name)
            elif h.is_file():
                shapes[h.name] = "f"
                if h.stat().st_size == 0:
                    zero_files.add(h.name)
            else:
                shapes[h.name] = "o"
        except PermissionError:
            continue
    return empty, zero_files, shapes


def container_bind_sources() -> tuple[dict[str, set[str]], dict[str, tuple[str, str]]]:
    """Returns (refs, vm_outers):
    refs: mount-dir hash -> set of referencing container names.
    vm_outers: outer double-hash -> (inner hash, recorded VM-path source),
    for mounts whose recorded source is itself a docker-desktop-bind-mounts
    VM path (the engine hashes THAT path a second time — clickhouse xml
    mounts 2026-11-24); the outer hash is the mount dir the container
    actually needs, rebound by binding the inner-hash VM path."""
    ids = docker("ps", "-aq").split()
    refs: dict[str, set[str]] = {}
    vm_outers: dict[str, tuple[str, str]] = {}
    for cid in ids:
        name = docker("inspect", "-f", "{{.Name}}", cid).strip().lstrip("/")
        mounts = docker("inspect", "-f", "{{json .Mounts}}", cid)
        try:
            for m in json.loads(mounts):
                if m.get("Type") != "bind":
                    continue
                src = m.get("Source", "")
                if f"docker-desktop-bind-mounts/{DISTRO}/" in src:
                    inner = src.rstrip("/").rsplit("/", 1)[-1]
                    outer = hashlib.sha256(src.encode()).hexdigest()
                    refs.setdefault(inner, set()).add(name)
                    refs.setdefault(outer, set()).add(name)
                    vm_outers[outer] = (inner, src)
                else:
                    h = hashlib.sha256(src.encode()).hexdigest()
                    refs.setdefault(h, set()).add(name)
        except Exception:  # noqa: BLE001
            continue
    return refs, vm_outers


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
    if src.rstrip("/").endswith("/docker.sock"):
        return "/run/guest-services/docker.container-proxy.sock"
    if src.startswith("/run/desktop/mnt/host/wsl/"):
        # already a VM-side path (outer double-hash rebinds bind this path
        # itself — its inner-hash mount resolves the real content)
        return src
    if src.startswith("/mnt/c/") or src.startswith("C:\\") or src.startswith("C:/"):
        # windows path -> /mnt/host/c/...
        if src.startswith("/mnt/c/"):
            return "/mnt/host/c/" + src[len("/mnt/c/"):]
        return _win_src(src).replace("/mnt/c/", "/mnt/host/c/", 1)
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
                f'if [ -f "{target}" ]; then rm -f "{target}"; fi',
                f'mkdir -p "{target}"',
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


def _win_src(src: str) -> str:
    """Normalize a Windows-style source (C:\\x, C:/x, or mixed C:\\x/y — the
    engine records whichever separator the compose file used) to /mnt/c/x/y."""
    return src.replace("C:\\", "/mnt/c/").replace("C:/", "/mnt/c/").replace("\\", "/")


def source_kind(src: str) -> str:
    # /run/docker.sock and its /var/run alias (symlink on Ubuntu) both map
    # to the VM's docker.container-proxy.sock
    if src.rstrip("/").endswith("/docker.sock") or src == "docker.sock":
        return "sock"
    # /mnt/c sources: check on the windows side via /mnt/c
    check = src
    if src.startswith("/mnt/c/"):
        check = src  # visible directly in WSL
    elif src.startswith("C:\\") or src.startswith("C:/"):
        check = _win_src(src)
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


def recreate_casualties(casualties: list[str]) -> tuple[int, list[str]]:
    """Recreate compose-managed casualties so bind mounts re-materialize
    from healed hashed sources. Returns (recreated_count, ids needing a
    plain docker start). Non-compose containers and recreate failures are
    returned in the second element."""
    labels = docker(
        "inspect",
        "-f",
        "{{.Name}}|{{index .Config.Labels \"com.docker.compose.project\"}}"
        "|{{index .Config.Labels \"com.docker.compose.service\"}}"
        "|{{index .Config.Labels \"com.docker.compose.project.working_dir\"}}"
        "|{{index .Config.Labels \"com.docker.compose.project.config_files\"}}",
        *casualties,
    )
    groups: dict[tuple[str, str, str], list[tuple[str, str]]] = {}
    dead: list[str] = []
    recreated = 0
    id_by_name: dict[str, str] = {}
    for line, cid in zip(labels.splitlines(), casualties):
        parts = line.strip().lstrip("/").split("|")
        name = parts[0] if parts else cid
        id_by_name[name] = cid
        project, service, workdir = (parts[1:4] if len(parts) >= 4 else ("", "", ""))
        cfg = parts[4] if len(parts) > 4 else ""
        if project and service and workdir and Path(workdir).is_dir():
            groups.setdefault((project, workdir, cfg), []).append((service, name))
        else:
            dead.append(cid)

    for (project, workdir, cfg), svcs in groups.items():
        cmd = ["docker", "compose", "-p", project]
        for f in cfg.split(","):
            f = f.strip()
            if f:
                cmd += ["-f", f]
        cmd += ["up", "-d", "--force-recreate", "--no-deps", "--pull", "never"]
        cmd += [s for s, _ in svcs]
        try:
            r = subprocess.run(
                cmd, cwd=workdir, capture_output=True, text=True, timeout=300
            )
            names = ",".join(n for _, n in svcs)
            if r.returncode == 0:
                recreated += len(svcs)
                log(f"recreated {len(svcs)} via compose ({project}): {names}")
            else:
                dead += [id_by_name[n] for _, n in svcs]
                log(
                    f"compose recreate failed ({project}): {names} "
                    f"rc={r.returncode} {r.stderr.strip()[-200:]}"
                )
        except Exception as exc:  # noqa: BLE001
            dead += [id_by_name[n] for _, n in svcs]
            log(f"compose recreate error ({project}): {exc}")
    return recreated, dead


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
        empty_dirs, zero_files, shapes = empty_hashes()
    else:
        empty_dirs, zero_files, shapes = probed

    state = _load_state()
    refs, vm_outers = container_bind_sources()
    journal = journal_hash_map()
    shas = sha_map()

    # learned map persisted across runs
    learned = state.setdefault("hashmap", {})
    for h, s in journal.items():
        learned[h] = s
    for h, s in shas.items():
        learned.setdefault(h, s)

    # outer double-hashes bind from their recorded VM-path source; their
    # expected node kind is the INNER hash's real-source kind
    kind_override: dict[str, str] = {}
    for outer, (inner, vsrc) in vm_outers.items():
        learned.setdefault(outer, vsrc)
        inner_src = learned.get(inner)
        if inner_src:
            kind_override[outer] = source_kind(inner_src)

    def kind_for(h: str, src: str) -> str:
        return kind_override.get(h) or source_kind(src)

    suspects = set(empty_dirs) | set(zero_files)
    # wrong-shape + missing poison: a hash dir materializing where the
    # source is a file/sock (docker start: "mount a directory onto a
    # file"), a file where a dir is expected, or the node gone entirely.
    # Invisible to the empty/zero probe — 2026-11-24 wave signature.
    shape_bad: set[str] = set()
    for h in refs:
        src = learned.get(h)
        if not src:
            continue
        want = kind_for(h, src)
        got = shapes.get(h)  # 'd'|'f'|'o'|None(missing)
        if got is None:
            shape_bad.add(h)
        elif want == "dir" and got != "d":
            shape_bad.add(h)
        elif want in ("file", "sock") and got == "d":
            shape_bad.add(h)
    suspects |= shape_bad
    if shape_bad:
        log(f"shape-suspect mounts (wrong/missing node): {[h[:12] for h in sorted(shape_bad)]}")

    if not suspects:
        return 0  # healthy — cheap exit

    now = time.time()
    last = state.get("last_rebind", 0)
    if now - last < MIN_INTERVAL:
        # still poisoned right after a heal attempt -> alert once, don't loop
        still = sorted(suspects)
        alert("rebind-stuck", f"fsbridge still poisoned {int(now-last)}s after rebind — {len(still)} suspect mounts: {', '.join(h[:8] for h in still)}")
        return 0

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
        if kind_for(h, src) == "dir":
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
        plan.append((h, vm_src, kind_for(h, src)))

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

    # restart casualties — exited or crash-looping on exit 127 (fsbridge
    # poison signature). `docker start` preserves the container's recorded
    # mount table, which keeps the poisoned mount → casualty re-dies on the
    # same error (verified 2026-11-24). Compose-managed casualties get
    # `up -d --force-recreate --no-deps` so mounts re-materialize from the
    # freshly-healed hashed source; non-compose containers fall back to
    # docker start.
    casualties: list[str] = []
    for cid in docker(
        "ps", "-aq",
        "--filter", "status=exited",
        "--filter", "status=restarting",
        "--filter", "status=created",
    ).split():
        st = docker("inspect", "-f", "{{.State.Status}}|{{.State.ExitCode}}", cid).strip()
        status, _, code = st.partition("|")
        if code == "127":
            casualties.append(cid)
        elif status == "created":
            # created-but-never-started during the poison wave (2026-11-24:
            # instagram-private-api) — only compose-managed ones, where
            # "created" unambiguously means "should be running"
            if docker("inspect", "-f",
                      '{{index .Config.Labels "com.docker.compose.project"}}',
                      cid).strip():
                casualties.append(cid)
    recreated = 0
    if casualties:
        recreated, still_dead = recreate_casualties(casualties)
        if still_dead:
            r = subprocess.run(
                ["docker", "start", *still_dead],
                capture_output=True, text=True, timeout=120,
            )
            log(
                f"plain-started {len(still_dead)} non-compose casualties: "
                f"{r.stdout.strip()[:120]} {r.stderr.strip()[:120]}"
            )

    ok_n = sum(1 for l in out.splitlines() if l.startswith("OK"))
    skip_n = sum(1 for l in out.splitlines() if l.startswith("SKIP"))
    real_binds = ok_n > 0 or any(l.startswith("FAIL") for l in out.splitlines())
    # throttle only on real rebind activity — skip/orphan-only passes must
    # not delay the next genuine poison wave
    if real_binds:
        state["last_rebind"] = now
        _save_state(state)
    log(f"heal complete: {ok_n} mounts rebound, {skip_n} skipped (empty source), {recreated} recreated, {len(casualties) - recreated} started")
    if ok_n or orphans:
        alert("healed", f"fsbridge auto-heal: {ok_n} mounts rebound, {len(orphans)} orphans cleaned, {len(casualties)} casualties recovered ({recreated} recreated)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
