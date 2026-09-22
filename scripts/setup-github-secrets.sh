#!/bin/bash
# Setup GitHub Secrets for CI/CD
# Run this script after installing GitHub CLI (gh) and authenticating
#
# Post-GHCR-cutover: image push uses the built-in GITHUB_TOKEN / packages:write
# permission. No Docker Hub secrets are required for CI. Keep this script for
# optional third-party secrets (Codecov, etc.).

set -euo pipefail

REPO="Themis128/cu130-slim"  # Adjust if different

echo "=== GitHub Secrets Setup for cu130-slim ==="
echo ""
echo "This script will add the following secrets to $REPO:"
echo "  1. CODECOV_TOKEN"
echo ""
echo "Note: GHCR push uses the built-in GITHUB_TOKEN — no DOCKERHUB_* secrets needed."
echo ""

# Check if gh is installed
if ! command -v gh &> /dev/null; then
    echo "❌ GitHub CLI (gh) not installed."
    echo "   Install it: https://cli.github.com/"
    echo ""
    echo "   Or add secrets manually at:"
    echo "   https://github.com/$REPO/settings/secrets/actions"
    exit 1
fi

# Check if authenticated
if ! gh auth status &> /dev/null; then
    echo "❌ Not authenticated with GitHub CLI."
    echo "   Run: gh auth login"
    exit 1
fi

echo "✅ GitHub CLI is installed and authenticated"
echo ""

# CODECOV_TOKEN
echo "1. CODECOV_TOKEN"
echo "   Get token from: https://codecov.io/gh/$REPO/settings"
read -s -p "   Enter Codecov token (or press Enter to skip): " CODECOV_TOKEN
echo ""
if [[ -n "$CODECOV_TOKEN" ]]; then
    gh secret set CODECOV_TOKEN --body "$CODECOV_TOKEN" --repo "$REPO"
    echo "   ✅ Set CODECOV_TOKEN"
else
    echo "   ⚠️  Skipped CODECOV_TOKEN (coverage upload will fail)"
fi
echo ""

# Verify secrets
echo "=== Verifying secrets ==="
gh secret list --repo "$REPO"
echo ""
echo "✅ All done! GHCR workflows use the built-in GITHUB_TOKEN."
echo "   Make GHCR packages public: https://github.com/Themis128?tab=packages"
