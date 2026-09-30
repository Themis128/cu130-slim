#!/usr/bin/env python3
"""Setup GitHub Secrets for CI/CD.
Run this script after installing GitHub CLI (gh) and authenticating.

Post-GHCR-cutover: image push uses the built-in GITHUB_TOKEN /
packages:write permission. No Docker Hub secrets are required for CI.
Keep this script for optional third-party secrets (Codecov, etc.).

Usage: setup-github-secrets.py"""

import getpass
import shutil
import subprocess
import sys

REPO = "Themis128/cu130-slim"  # Adjust if different

print(f"=== GitHub Secrets Setup for cu130-slim ===\n")
print(f"This script will add the following secrets to {REPO}:")
print("  1. CODECOV_TOKEN\n")
print("Note: GHCR push uses the built-in GITHUB_TOKEN — "
      "no DOCKERHUB_* secrets needed.\n")

if not shutil.which("gh"):
    print("❌ GitHub CLI (gh) not installed.")
    print("   Install it: https://cli.github.com/\n")
    print("   Or add secrets manually at:")
    print(f"   https://github.com/{REPO}/settings/secrets/actions")
    sys.exit(1)

if subprocess.run(["gh", "auth", "status"], capture_output=True).returncode:
    print("❌ Not authenticated with GitHub CLI.")
    print("   Run: gh auth login")
    sys.exit(1)

print("✅ GitHub CLI is installed and authenticated\n")

print("1. CODECOV_TOKEN")
print(f"   Get token from: https://codecov.io/gh/{REPO}/settings")
token = getpass.getpass("   Enter Codecov token (or press Enter to skip): ")
if token:
    subprocess.run(["gh", "secret", "set", "CODECOV_TOKEN",
                    "--body", token, "--repo", REPO], check=True)
    print("   ✅ Set CODECOV_TOKEN")
else:
    print("   ⚠️  Skipped CODECOV_TOKEN (coverage upload will fail)")
print()

print("=== Verifying secrets ===")
subprocess.run(["gh", "secret", "list", "--repo", REPO])
print("\n✅ All done! GHCR workflows use the built-in GITHUB_TOKEN.")
