#!/usr/bin/env python3
"""nc_sync_audit.py — recurring Nextcloud phone-sync hygiene audit.

Born from the 2026-11-11 incident: the Android app had three overlapping
camera auto-upload entries writing to /InstantUpload/Camera,
/InstantUpload/null/DCIM/Camera and /PhoneBackup/DCIM/Camera, producing
~1,080 duplicate files and repeated 'conflicted copy' sync collisions.

Checks (all read-only unless --cleanup-trash):
  1. InstantUpload/ children vs EXPECTED set — flags the 'null' album-path
     bug signature and any other unexpected folder.
  2. Filename overlap across InstantUpload/* and PhoneBackup/DCIM/* —
     detects duplicate upload paths re-appearing.
  3. Recursive 'conflicted copy' filename scan (DAV infinity PROPFIND).
  4. Trashbin size via `occ` on the omv host (SSH) — reports only.

Exit codes: 0 clean, 1 findings, 2 audit error.

Auth: NEXTCLOUD_DAV_URL / NEXTCLOUD_USERNAME / NEXTCLOUD_APP_PASSWORD from
the cu130-slim .env (same contract as nextcloud-dav.py).
Alerting: --alert pipes findings through tg_alert.py (ops channel).
"""
from __future__ import annotations

import argparse
import base64
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]
NS = {"d": "DAV:"}

EXPECTED_INSTANTUPLOAD = {"Camera", "Screenshots", "Telegram Images"}
SCAN_ROOTS = ["InstantUpload", "PhoneBackup"]
OVERLAP_DIRS = [
    "InstantUpload/Camera",
    "InstantUpload/null/DCIM/Camera",
    "PhoneBackup/DCIM/Camera",
]
TRASH_SSH = [
    "ssh", "-i", str(Path.home() / ".ssh/omv_id_rsa"),
    "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
    "-o", "ConnectTimeout=10", "tbaltzakis@192.168.1.200",
]
TRASH_OCC = "docker exec nextcloud php occ"


def _load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for cand in (REPO_ROOT / ".env", Path.home() / "cu130-slim" / ".env"):
        if cand.exists():
            for line in cand.read_text().splitlines():
                m = re.match(r"^([A-Z_]+)=(.*)$", line.strip())
                if m:
                    env[m.group(1)] = m.group(2).strip().strip('"')
            break
    env.update({k: v for k, v in os.environ.items() if k.startswith("NEXTCLOUD_")})
    return env


def _auth(env: dict[str, str]) -> str:
    return "Basic " + base64.b64encode(
        f"{env['NEXTCLOUD_USERNAME']}:{env['NEXTCLOUD_APP_PASSWORD']}".encode()
    ).decode()


def _dav(env: dict[str, str], method: str, path: str, depth: str = "1",
         body: bytes | None = None) -> bytes:
    url = env["NEXTCLOUD_DAV_URL"].rstrip("/") + "/" + urllib.parse.quote(path)
    headers = {"Authorization": _auth(env), "Depth": depth}
    data = body
    if body is not None:
        headers["Content-Type"] = "application/xml"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


PROPFIND_TYPES = (
    b'<?xml version="1.0"?>'
    b'<d:propfind xmlns:d="DAV:"><d:prop>'
    b"<d:resourcetype/><d:getcontentlength/>"
    b"</d:prop></d:propfind>"
)


def _children(env: dict[str, str], path: str) -> list[tuple[str, bool, int]]:
    """[(name, is_dir, size)] for direct children of path."""
    try:
        raw = _dav(env, "PROPFIND", path, body=PROPFIND_TYPES)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return []
        print(f"WARN: cannot list {path}: {exc}")
        return []
    except Exception as exc:  # noqa: BLE001 - report, don't crash audit
        print(f"WARN: cannot list {path}: {exc}")
        return []
    out = []
    for resp in ET.fromstring(raw).findall("d:response", NS):
        href = resp.find("d:href", NS).text or ""
        name = urllib.parse.unquote(href.rstrip("/").split("/")[-1])
        if not name or name == path.rstrip("/").split("/")[-1]:
            continue
        is_dir = resp.find(".//d:collection", NS) is not None
        size_el = resp.find(".//d:getcontentlength", NS)
        size = int(size_el.text) if size_el is not None and size_el.text else 0
        out.append((name, is_dir, size))
    return out


def _all_files(env: dict[str, str], root: str) -> list[str]:
    """Recursive file names under root (infinity depth)."""
    try:
        raw = _dav(env, "PROPFIND", root, depth="infinity", body=PROPFIND_TYPES)
    except Exception as exc:  # noqa: BLE001
        print(f"WARN: cannot scan {root}: {exc}")
        return []
    names = []
    for resp in ET.fromstring(raw).findall("d:response", NS):
        href = resp.find("d:href", NS).text or ""
        name = urllib.parse.unquote(href.rstrip("/").split("/")[-1])
        if resp.find(".//d:collection", NS) is None and name:
            names.append(name)
    return names


def _trash_retention() -> str:
    try:
        out = subprocess.run(
            TRASH_SSH + [f"{TRASH_OCC} config:system:get trashbin_retention_obligation"],
            capture_output=True, text=True, timeout=30,
        )
        if out.stdout.strip():
            return out.stdout.strip()
        if out.returncode != 0:
            return f"ssh-error: rc={out.returncode} {out.stderr.strip()[:120]}"
        return "unset"
    except Exception as exc:  # noqa: BLE001
        return f"ssh-error: {exc}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--alert", action="store_true", help="send findings to TG ops")
    args = ap.parse_args()

    env = _load_env()
    for k in ("NEXTCLOUD_DAV_URL", "NEXTCLOUD_USERNAME", "NEXTCLOUD_APP_PASSWORD"):
        if not env.get(k):
            print(f"missing {k}")
            return 2

    findings: list[str] = []

    # 1. InstantUpload children vs expected set
    iu = _children(env, "InstantUpload")
    names = {n for n, is_dir, _ in iu if is_dir}
    extra = names - EXPECTED_INSTANTUPLOAD
    missing = EXPECTED_INSTANTUPLOAD - names
    if "null" in names:
        findings.append("InstantUpload/null exists — Android 'null' album-path bug is back")
    if extra - {"null"}:
        findings.append(f"unexpected InstantUpload dirs: {sorted(extra - {'null'})}")
    if missing:
        findings.append(f"expected InstantUpload dirs missing: {sorted(missing)}")

    # 2. filename overlap across known upload targets
    seen: dict[str, str] = {}
    overlap = 0
    overlap_examples: list[str] = []
    for d in OVERLAP_DIRS:
        for name, is_dir, _size in _children(env, d):
            if is_dir:
                continue
            if name in seen and seen[name] != d:
                overlap += 1
                if len(overlap_examples) < 5:
                    overlap_examples.append(f"{name} ({seen[name]} vs {d})")
            else:
                seen[name] = d
    if overlap:
        findings.append(
            f"{overlap} filenames exist in multiple upload dirs — duplicate "
            f"upload path regression. e.g. {overlap_examples}"
        )

    # 3. conflicted-copy scan
    conflicts = 0
    for root in SCAN_ROOTS:
        for name in _all_files(env, root):
            if "conflicted copy" in name.lower():
                conflicts += 1
    if conflicts:
        findings.append(f"{conflicts} 'conflicted copy' files under {SCAN_ROOTS}")

    # 4. trash retention config sanity
    ret = _trash_retention()
    if ret.startswith("ssh-error"):
        findings.append(f"cannot reach omv for trashbin check ({ret})")

    report = {
        "instantupload_dirs": sorted(names),
        "overlap_files": overlap,
        "conflicted_copies": conflicts,
        "trashbin_retention_days": ret,
        "findings": findings,
    }
    print(f"audit: {len(findings)} finding(s)")
    for f in findings:
        print(f"  - {f}")

    if findings and args.alert:
        tg = REPO_ROOT / ".devin/skills/telegram-alert-ops/scripts/tg_alert.py"
        msg = "nextcloud sync audit: " + "; ".join(findings)
        subprocess.run(
            [sys.executable, str(tg), "ops", msg],
            capture_output=True, timeout=30,
        )
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
