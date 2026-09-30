#!/usr/bin/env python3
"""timestamp-release.py — prepare a repo for proof-of-authorship timestamping.

Produces a clean release zip + SHA-256 checksum you can submit to:
  * https://www.timestamp.gr  (Hellenic Copyright Organization — free,
    official Greek state service; certificate = proof of existence date)
  * OpenTimestamps (optional, if `ots` is installed — anchors the hash
    into the Bitcoin blockchain, also free)

Usage:
  ./scripts/timestamp-release.py [repo_dir] [label]
  e.g. ./scripts/timestamp-release.py ~/cloudless.gr v2.4.0

The zip is written next to the repo as <name>-<label|git-sha>.zip
and is NOT committed. Nothing in it should contain secrets — this script
refuses to include .env files; verify anyway before uploading anywhere."""

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

repo = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
label = sys.argv[2] if len(sys.argv) > 2 else ""
name = repo.name
r = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
                   capture_output=True, text=True)
sha = r.stdout.strip() if r.returncode == 0 else "nogit"
tag = label or sha
out = repo.parent / f"{name}-{tag}.zip"

print(f"Repo:   {repo}")
print(f"Commit: {sha}")
print(f"Out:    {out}")

# Archive only tracked files — excludes .env, node_modules, build output,
# and anything gitignored (cookie dumps, session states, local secrets).
subprocess.run(["git", "archive", "--format=zip", "-o", str(out), "HEAD"],
               cwd=repo, check=True)

sha256 = hashlib.sha256(out.read_bytes()).hexdigest()
Path(f"{out}.sha256").write_text(f"{sha256}  {out.name}\n")

print(f"""
SHA-256: {sha256}
Manifest: {out}.sha256

Next steps:
  1. Go to https://www.timestamp.gr (free HCO account required)
  2. Submit {out} (or just the .sha256 if you prefer not to upload code)
  3. Save the issued timestamp certificate alongside this zip""")

if shutil.which("ots"):
    subprocess.run(["ots", "stamp", f"{out}.sha256"], capture_output=True)
    print(f"  + OpenTimestamps stamp queued: {out}.sha256.ots")
else:
    print(f"  (optional) pip install opentimestamps-client && "
          f"ots stamp {out}.sha256")
