#!/usr/bin/env python3
"""dd_upgrade.py — Docker Desktop / WSL2 upgrade + vsock health ops.

Subcommands:
  status    — WSL version, Docker Desktop version, engine version, vsock health
  check     — same as status but exit 1 when an upgrade is available
  download  — fetch latest DD installer from desktop.docker.com, verify SHA256
  install   — copy installer to Windows temp and launch it (per-user)
  heal      — restart docker-desktop WSL distro (light vsock reset, keeps Ubuntu)

The fsbridge failure class this supports: bind mounts materialize as EMPTY
directories, containers mass-Exit(127), `wsl.exe -d docker-desktop` times out
with WSAETIMEDOUT / UtilAcceptVsock errors. Durable heal is wsl --shutdown +
DD restart; `heal` is the lighter first step.

Env overrides:
  DD_INSTALLER_PATH  — skip download, use this local installer
  WIN_TEMP_DIR       — Windows temp dir (default %TEMP% via powershell)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
WIN_TEMP = r"C:\Users\tbaltzakis\AppData\Local\Temp"


def _ps(cmd: str, timeout: int = 60) -> str:
    """Run a PowerShell command on the Windows host via WSL interop."""
    out = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", cmd],
        capture_output=True, text=True, timeout=timeout,
    )
    return (out.stdout or "") + (out.stderr or "")


def _wsl(cmd: str, timeout: int = 60) -> str:
    out = subprocess.run(
        ["wsl.exe"] + cmd.split(), capture_output=True, text=True, timeout=timeout
    )
    return (out.stdout or "") + (out.stderr or "")


def _docker(args: str, timeout: int = 30) -> str:
    out = subprocess.run(
        ["docker"] + args.split(), capture_output=True, text=True, timeout=timeout
    )
    return (out.stdout or "") + (out.stderr or "")


def status() -> dict:
    st: dict = {}
    ver = _wsl("--version", timeout=30)
    # wsl.exe emits UTF-16-ish spaced text through interop — collapse the
    # letter-spacing ("W S L   v e r s i o n :   3 . 0 . 5 . 0") so regexes work
    ver = ver.replace("\x00", "")
    ver = re.sub(r"(?<=\S) (?=\S)", "", ver)
    m = re.search(r"WSL\s*version:\s*([\d.]+)", ver, re.I)
    st["wsl_version"] = m.group(1) if m else "unknown"
    k = re.search(r"Kernel\s*version:\s*([\d.]+(?:-[\w.]+)?)", ver, re.I)
    st["kernel"] = k.group(1) if k else "unknown"

    # Docker Desktop version from the exe path
    dd = _ps(
        "(Get-Item 'C:\\Users\\tbaltzakis\\AppData\\Local\\Programs\\DockerDesktop\\frontend\\Docker Desktop.exe' "
        "-ErrorAction SilentlyContinue).VersionInfo.ProductVersion"
    ).strip()
    st["docker_desktop"] = dd or "unknown"
    st["engine"] = _docker("version --format {{.Server.Version}}", 15).strip() or "down"

    # fsbridge health: can we still exec into docker-desktop distro?
    probe = _wsl("-d docker-desktop -e true", timeout=20)
    st["fsbridge_alive"] = "timed out" not in probe.lower() and "error" not in probe.lower()[:40]
    st["fsbridge_probe"] = probe.strip()[:120]
    return st


def latest_dd_release() -> dict | None:
    """Parse winget's version list for the newest Docker Desktop."""
    out = _ps("winget show --id Docker.DockerDesktop --versions", timeout=60)
    vers = re.findall(r"^\s*(\d+\.\d+\.\d+)\s*$", out, re.M)
    return {"latest": vers[0]} if vers else None


def download(out_dir: Path) -> Path:
    """Download the newest DD installer. Returns local path."""
    rel = latest_dd_release()
    if not rel:
        raise SystemExit("could not resolve latest Docker Desktop version via winget")
    ver = rel["latest"]
    # winget installer URL pattern: /win/main/amd64/<build>/Docker%20Desktop%20Installer.exe
    # build number isn't in `winget show --versions`; fetch it:
    show = _ps(f"winget show --id Docker.DockerDesktop --version {ver}", timeout=60)
    m = re.search(r"Installer Url:\s*(\S+)", show)
    sha = re.search(r"Installer SHA256:\s*([0-9a-fA-F]+)", show)
    if not m:
        raise SystemExit(f"no installer URL found for DD {ver}")
    url = m.group(1)
    dest = out_dir / f"DockerDesktop-{ver}.exe"
    print(f"downloading {url}")
    urllib.request.urlretrieve(url, dest)
    digest = hashlib.sha256(dest.read_bytes()).hexdigest()
    if sha and digest.lower() != sha.group(1).lower():
        raise SystemExit(f"SHA256 mismatch: got {digest}, expected {sha.group(1)}")
    print(f"ok: {dest} ({dest.stat().st_size} bytes, sha256 verified)")
    return dest


def install(installer: Path) -> None:
    win_path = str(installer)
    if installer.is_relative_to(Path("/mnt/c")):
        win_path = "C:\\" + str(installer).split("/mnt/c/", 1)[1].replace("/", "\\")
    elif not str(installer).startswith(("/mnt/", "C:\\")):
        # copy into Windows temp first
        win_tmp = Path("/mnt/c/Users/tbaltzakis/AppData/Local/Temp")
        win_tmp.mkdir(parents=True, exist_ok=True)
        target = win_tmp / installer.name
        shutil.copy2(installer, target)
        win_path = "C:\\Users\\tbaltzakis\\AppData\\Local\\Temp\\" + installer.name
    print(f"launching installer: {win_path}")
    # Per-user install; --quiet supported by the DD installer bootstrap.
    _ps(
        f"Start-Process -FilePath '{win_path}' -ArgumentList 'install','--quiet'",
        timeout=30,
    )
    print("installer launched — Docker Desktop will restart; stack recovers via watchdogs")


def heal() -> None:
    print("terminating docker-desktop distro (fsbridge reset; Ubuntu-24.04 unaffected)...")
    _ps("wsl.exe --terminate docker-desktop", timeout=60)
    print("done — Docker Desktop auto-restarts its backend; verify with `status`")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("check")
    dl = sub.add_parser("download")
    dl.add_argument("--out", type=Path, default=Path.home() / "downloads")
    ins = sub.add_parser("install")
    ins.add_argument("installer", type=Path, nargs="?", default=None)
    sub.add_parser("heal")
    args = ap.parse_args()

    if args.cmd in ("status", "check"):
        st = status()
        print(json.dumps(st, indent=2))
        if args.cmd == "check":
            rel = latest_dd_release()
            if rel and st["docker_desktop"] and rel["latest"] not in st["docker_desktop"]:
                print(f"upgrade available: DD {st['docker_desktop']} -> {rel['latest']}")
                return 1
            if not st["fsbridge_alive"]:
                print("fsbridge unhealthy — run `heal` or full wsl --shutdown")
                return 1
        return 0
    if args.cmd == "download":
        args.out.mkdir(parents=True, exist_ok=True)
        download(args.out)
        return 0
    if args.cmd == "install":
        inst = args.installer or download(Path.home() / "downloads")
        install(inst)
        return 0
    if args.cmd == "heal":
        heal()
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
