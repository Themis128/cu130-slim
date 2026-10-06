#!/usr/bin/env python3
"""Verify patchright deployment across browser-novnc, sidecars, and
backend consumers. Exit 1 if any hard check fails; warnings are non-fatal.
Usage: verify.py"""

import glob
import json
import py_compile
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import repo_root  # noqa: E402

ROOT = repo_root()
ERR = 0


def warn(msg: str) -> None:
    print(f"[WARN] {msg}")


def fail(msg: str) -> None:
    global ERR
    print(f"[FAIL] {msg}")
    ERR = 1


def ok(msg: str) -> None:
    print(f"[OK]   {msg}")


def get(url: str, timeout: int = 5) -> bytes | None:
    try:
        return urllib.request.urlopen(url, timeout=timeout).read()
    except Exception:
        return None


# 1. Health endpoints
for ep in ("http://localhost:9223/health", "http://localhost:9224/health",
           "http://localhost:9225/health", "http://localhost:9226/health"):
    if get(ep) is not None:
        ok(f"health {ep}")
    else:
        fail(f"health {ep} unreachable")

# 2. Bridge webdriver flag must be false
bridge_owner = "unknown"
raw = get("http://localhost:9223/session/status")
if raw:
    try:
        bridge_owner = json.loads(raw).get("platform", "unknown")
    except Exception:
        pass
if bridge_owner and bridge_owner != "unknown":
    wd = "NO_RESULT"
    try:
        req = urllib.request.Request(
            "http://localhost:9223/session/evaluate",
            data=b'{"expression":"navigator.webdriver"}',
            headers={"Content-Type": "application/json",
                     "X-Platform": bridge_owner}, method="POST")
        wd = json.loads(urllib.request.urlopen(req, timeout=10)
                        .read()).get("result", "NO_RESULT")
    except Exception:
        pass
    if str(wd).lower() == "false":
        ok(f"navigator.webdriver=false on bridge (owner={bridge_owner})")
    else:
        fail(f"navigator.webdriver={wd} on bridge — patchright may not be active")
else:
    warn("could not determine bridge owner; skipping webdriver check")

# 3. Python consumer imports compile
py_files = [
    "browser-novnc/browser-bridge.py",
    "browser-novnc/extract-cookies.py",
    "social-automation/backend/app/services/browser_profile.py",
    "social-automation/backend/app/services/tiktok_bio_update.py",
    "social-automation/backend/app/services/tiktok_captcha_analyze.py",
    "social-automation/backend/app/services/tiktok_captcha_api.py",
    "social-automation/backend/app/services/tiktok_captcha_debug.py",
    "social-automation/backend/app/services/tiktok_captcha_debug2.py",
    "social-automation/backend/app/services/tiktok_captcha_debug3.py",
]
for f in py_files:
    try:
        py_compile.compile(str(ROOT / f), doraise=True)
        ok(f"compile {f}")
    except Exception:
        fail(f"compile {f}")

# 4. Python patchright available inside social-api
r = subprocess.run(
    ["docker", "compose", "exec", "-T", "social-api", "python3", "-c",
     "from patchright.async_api import async_playwright; print('patchright ok')"],
    cwd=ROOT, capture_output=True, text=True)
if "patchright" in r.stdout:
    ok("patchright import inside social-api")
else:
    fail("patchright not importable inside social-api")

# 5. Sidecars run patchright package (Node)
for svc in ("tiktok-browser-sidecar", "linkedin-browser-sidecar",
            "facebook-browser-sidecar"):
    r = subprocess.run(
        ["docker", "exec", svc, "sh", "-c",
         'node -e "import(\\"patchright\\").then(()=>console.log(\\"ok\\"))"'],
        capture_output=True, text=True)
    if "ok" in r.stdout:
        ok(f"patchright node import inside {svc}")
    else:
        fail(f"patchright not importable inside {svc}")

# 6. No stray bare playwright imports in consumers (except ImportError
#    fallback blocks are intentional)
files = [
    "browser-novnc/browser-bridge.py",
    "browser-novnc/extract-cookies.py",
    *glob.glob(str(ROOT / "social-automation/backend/app/services/tiktok_*.py")),
    "social-automation/backend/app/services/browser_profile.py",
    "tiktok-browser-sidecar/server.js",
    "linkedin-browser-sidecar/server.js",
    "facebook-browser-sidecar/server.js",
]
strays = []
for path in files:
    p = Path(path) if Path(path).is_absolute() else ROOT / path
    try:
        lines = p.read_text().splitlines()
    except FileNotFoundError:
        continue
    except_indent = None
    for i, line in enumerate(lines):
        if except_indent is not None:
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            cur_indent = len(line) - len(line.lstrip())
            if cur_indent <= except_indent:
                except_indent = None
        if except_indent is None and re.search(r"^\s*except\s+ImportError", line):
            except_indent = len(line) - len(line.lstrip())
            continue
        if re.search(r"from\s+playwright", line) and except_indent is None:
            strays.append(f"{p}:{i + 1}: {line.strip()}")

if strays:
    fail("bare playwright imports found:\n" + "\n".join(strays))
else:
    ok("no bare playwright imports in consumers")

# 7. browser_profile volume present in compose
r = subprocess.run(["docker", "compose", "config"], cwd=ROOT,
                   capture_output=True, text=True)
if "browser_profile:" in r.stdout:
    ok("browser_profile volume declared in compose")
else:
    fail("browser_profile volume missing from compose")

# 8. browser-novnc container actually mounts the profile
r = subprocess.run(
    ["docker", "inspect", "browser-novnc",
     "--format", '{{range .Mounts}}{{.Destination}}{{"\\n"}}{{end}}'],
    capture_output=True, text=True)
if "/app/browser-profile" in r.stdout:
    ok("browser-novnc mounts /app/browser-profile")
else:
    fail("browser-novnc does not mount /app/browser-profile")

print("[ALL OK] patchright deployment verified" if ERR == 0
      else "[DONE] some checks failed")
sys.exit(ERR)
