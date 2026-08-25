# GitHub Secrets Setup Instructions

## Required Secrets for CI/CD

| Secret | Required | Purpose | Where to Get |
|--------|----------|---------|--------------|
| `DOCKERHUB_USERNAME` | ✅ Yes | Docker Hub username for image pushes | Your Docker Hub account |
| `DOCKERHUB_TOKEN` | ✅ Yes | Docker Hub access token for authentication | https://hub.docker.com/settings/security |
| `CODECOV_TOKEN` | ⚠️ Optional | Codecov upload token for coverage reports | https://codecov.io/gh/Themis128/ComfyUI-Docker/settings |

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

1. Go to: **https://github.com/Themis128/ComfyUI-Docker/settings/secrets/actions**

2. Click **"New repository secret"** for each:

### DOCKERHUB_USERNAME
- **Name**: `DOCKERHUB_USERNAME`
- **Secret**: `baltzakist`

### DOCKERHUB_TOKEN
- **Name**: `DOCKERHUB_TOKEN`
- **Secret**: [Create at https://hub.docker.com/settings/security]
  - Click "New Access Token"
  - Name: `github-actions-cu130-slim`
  - Permissions: **Read, Write, Delete**
  - Copy the token immediately (shown only once!)

### CODECOV_TOKEN (Optional)
- **Name**: `CODECOV_TOKEN`
- **Secret**: [Get from https://codecov.io/gh/Themis128/ComfyUI-Docker/settings]
  - Repository upload token

---

## Verify Secrets Are Set

```bash
# Via CLI
gh secret list --repo Themis128/ComfyUI-Docker

# Or check in UI: https://github.com/Themis128/ComfyUI-Docker/settings/secrets/actions
```

Expected output:
```
NAME              UPDATED
CODECOV_TOKEN     2024-01-15 10:30:00
DOCKERHUB_TOKEN   2024-01-15 10:30:00
DOCKERHUB_USERNAME 2024-01-15 10:30:00
```

---

## After Secrets Are Set

1. **Trigger first build** (push to master or run manually):
   ```bash
   # Option A: Push to master
   git push origin master
   
   # Option B: Manual trigger via CLI
   gh workflow run docker-ci.yml --repo Themis128/ComfyUI-Docker
   
   # Option C: Manual trigger via UI
   # Go to Actions → Docker CI → Run workflow
   ```

2. **Monitor the build**:
   - https://github.com/Themis128/ComfyUI-Docker/actions

3. **Verify images on Docker Hub**:
   - https://hub.docker.com/r/baltzakist/cu130-slim-comfyui
   - https://hub.docker.com/r/baltzakist/cu130-slim-env-manager-backend
   - https://hub.docker.com/r/baltzakist/cu130-slim-env-manager-frontend
   - https://hub.docker.com/r/baltzakist/cu130-slim-social-api
   - https://hub.docker.com/r/baltzakist/cu130-slim-social-worker
   - https://hub.docker.com/r/baltzakist/cu130-slim-social-frontend

---

## Troubleshooting

### "Docker Hub rate limit exceeded"
- Ensure `DOCKERHUB_TOKEN` is set correctly
- Check token has Read/Write/Delete permissions
- Token must not be expired

### "Permission denied" on push
- Verify `DOCKERHUB_USERNAME` matches the token owner
- Check repository exists on Docker Hub (auto-created on first push)

### Codecov upload fails
- Ensure `CODECOV_TOKEN` is from the correct repository
- Token must have upload permissions

### Workflow not triggering
- Check branch protection rules
- Verify workflow files are in `.github/workflows/`
- Check Actions tab for disabled workflows