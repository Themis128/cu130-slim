#!/bin/bash
# Setup GitHub Secrets for CI/CD
# Run this script after installing GitHub CLI (gh) and authenticating

set -euo pipefail

REPO="Themis128/ComfyUI-Docker"  # Adjust if different

echo "=== GitHub Secrets Setup for cu130-slim ==="
echo ""
echo "This script will add the following secrets to $REPO:"
echo "  1. DOCKERHUB_USERNAME"
echo "  2. DOCKERHUB_TOKEN"
echo "  3. CODECOV_TOKEN"
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

# DOCKERHUB_USERNAME
echo "1. DOCKERHUB_USERNAME"
read -p "   Enter Docker Hub username (default: baltzakist): " DOCKERHUB_USERNAME
DOCKERHUB_USERNAME=${DOCKERHUB_USERNAME:-baltzakist}
gh secret set DOCKERHUB_USERNAME --body "$DOCKERHUB_USERNAME" --repo "$REPO"
echo "   ✅ Set DOCKERHUB_USERNAME=$DOCKERHUB_USERNAME"
echo ""

# DOCKERHUB_TOKEN
echo "2. DOCKERHUB_TOKEN"
echo "   Create a token at: https://hub.docker.com/settings/security"
echo "   (Access Token → New Access Token → Read, Write, Delete permissions)"
read -s -p "   Enter Docker Hub access token: " DOCKERHUB_TOKEN
echo ""
gh secret set DOCKERHUB_TOKEN --body "$DOCKERHUB_TOKEN" --repo "$REPO"
echo "   ✅ Set DOCKERHUB_TOKEN"
echo ""

# CODECOV_TOKEN
echo "3. CODECOV_TOKEN"
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
echo "✅ All done! Workflows will now use these secrets."