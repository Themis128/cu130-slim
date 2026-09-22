# GitHub Secrets Setup Instructions

## Required Secrets for CI/CD

| Secret | Required | Purpose | Where to Get |
|--------|----------|---------|--------------|
| `CODECOV_TOKEN` | ⚠️ Optional | Codecov upload token for coverage reports | https://codecov.io/gh/Themis128/cu130-slim/settings |

> **GHCR note:** Image pushes to GitHub Container Registry use the built-in
> `GITHUB_TOKEN` with the `packages: write` permission declared in each
> workflow — **no `DOCKERHUB_*` secrets are needed**. Older releases may still
> exist on Docker Hub under `baltzakist/`; new pushes go to GHCR only.

---

## Option 1: Using GitHub CLI (Recommended)

```bash
# Install GitHub CLI if not already installed
# macOS: brew install gh
# Linux: https://github.com/cli/cli/blob/trunk/docs/install_linux.md
# Windows: winget install GitHub.cli

# Authenticate
gh auth login

# Run the setup script
./scripts/setup-github-secrets.sh
```

---

## Option 2: Manual via GitHub Web UI

1. Go to: **https://github.com/Themis128/cu130-slim/settings/secrets/actions**

2. Click **"New repository secret"**:

### CODECOV_TOKEN (Optional)
- **Name**: `CODECOV_TOKEN`
- **Secret**: [Get from https://codecov.io/gh/Themis128/cu130-slim/settings]
  - Repository upload token

---

## Verify Secrets Are Set

```bash
# Via CLI
gh secret list --repo Themis128/cu130-slim

# Or check in UI: https://github.com/Themis128/cu130-slim/settings/secrets/actions
```

---

## Making GHCR packages public (one-time, per package)

Anonymous pulls (Trivy, `docker compose pull` without auth) need public packages:

1. Open https://github.com/Themis128?tab=packages
2. For each `cu130-slim-*` package → Package settings → Change visibility → **Public**

---

## After Secrets Are Set

1. **Trigger first build** (push to master or run manually):
   ```bash
   # Option A: Push to master
   git push origin master

   # Option B: Manual trigger via CLI
   gh workflow run build-and-push.yml --repo Themis128/cu130-slim

   # Option C: Manual trigger via UI
   # Go to Actions → Build and push images → Run workflow
   ```

2. **Monitor the build**:
   - https://github.com/Themis128/cu130-slim/actions

3. **Verify images on GHCR**:
   - https://github.com/Themis128?tab=packages
   - `docker manifest inspect ghcr.io/themis128/cu130-slim-<service>:latest`

---

## Troubleshooting

### "denied: permission_denied" on GHCR push
- Ensure the workflow declares `packages: write` under `permissions:`.
- Package visibility/org settings may restrict `GITHUB_TOKEN` writes — check
  repo Settings → Actions → Workflow permissions.

### Anonymous pull fails
- Package is still private — flip it to **Public** in package settings.

### Codecov upload fails
- Ensure `CODECOV_TOKEN` is from the correct repository.
- Token must have upload permissions.

### Workflow not triggering
- Check branch protection rules.
- Verify workflow files are in `.github/workflows/`.
- Check Actions tab for disabled workflows.
