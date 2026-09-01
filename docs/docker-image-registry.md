# Docker Image Registry

All CI-built images are hosted on Docker Hub under the `baltzakist` namespace.
CI auto-increments version tags on every push to `master` and keeps `latest`
as a moving alias for the newest build.

## Image naming convention

```
baltzakist/cu130-slim-<service>:<tag>
```

## Tag types

| Tag format        | Description                                      | Updated by         |
|-------------------|--------------------------------------------------|--------------------|
| `latest`          | Moving alias — always points to the newest build | Every CI run       |
| `v2.<run_number>` | Auto-incremented version tag (e.g. `v2.418`)     | Every CI run       |
| `sha-<git_sha>`   | Git commit SHA (e.g. `sha-309e26d`)              | Every CI run       |
| `master`          | Branch ref tag                                   | Every push to master |

## CI-built services (6 images)

| Service               | Docker Hub repository                           | Compose service         | Image size (compressed) |
|-----------------------|-------------------------------------------------|-------------------------|-------------------------|
| ComfyUI               | `baltzakist/cu130-slim-comfyui`                 | `comfyui`               | ~7.9 GB                 |
| Env Manager Backend   | `baltzakist/cu130-slim-env-manager-backend`     | `env-manager-backend`   | ~140 MB                 |
| Env Manager Frontend  | `baltzakist/cu130-slim-env-manager-frontend`    | `env-manager-frontend`  | ~31 MB                  |
| Social API            | `baltzakist/cu130-slim-social-api`              | `social-api`            | ~220 MB                 |
| Social Worker         | `baltzakist/cu130-slim-social-worker`           | `social-worker`         | ~220 MB                 |
| Social Frontend       | `baltzakist/cu130-slim-social-frontend`         | `social-frontend`       | ~84 MB                  |

`celery-beat` uses the same image as `social-worker`.

## Non-CI images (pinned, not auto-incremented)

| Service       | Image                          | Notes                        |
|---------------|--------------------------------|------------------------------|
| cloudflared   | `baltzakist/cloudflared:v0.2`  | Manually built, not in CI    |

## How Compose consumes the images

### `docker-compose.yml` (base)

Pinned to a specific version tag for reproducibility:

```yaml
image: baltzakist/cu130-slim-social-api:v2.417
```

All six CI-built services have `pull_policy: always` so `docker compose up -d`
always pulls the newest version of the pinned tag.

### `docker-compose.override.yml` (local dev, gitignored)

Two services are overridden to `:latest` for local development convenience:

```yaml
comfyui:
  image: baltzakist/cu130-slim-comfyui:latest
social-frontend:
  image: baltzakist/cu130-slim-social-frontend:latest
```

This means:
- **comfyui** and **social-frontend** always pull the newest `latest` image.
- The other four services pull the version pinned in `docker-compose.yml`.

## How to update all images to the latest version

### Option 1: Pin to a specific version (recommended for production)

1. Check Docker Hub for the latest `v2.<run_number>` tag.
2. Update `docker-compose.yml` to use that tag.
3. Run:

```bash
docker compose pull && docker compose up -d
```

### Option 2: Use `latest` (always newest)

Change all image tags in `docker-compose.yml` to `:latest`, then:

```bash
docker compose pull && docker compose up -d
```

### Option 3: Pull and recreate without changing compose

```bash
docker compose pull
docker compose up -d --force-recreate
```

## Current tag snapshot (as of 2026-09-01)

Latest CI build: **v2.418** (run #418, commit `309e26d`)

| Service               | Latest tag | Compose uses     | Override uses |
|-----------------------|------------|------------------|---------------|
| comfyui               | v2.418     | v2.417           | latest        |
| env-manager-backend   | v2.418     | v2.417           | —             |
| env-manager-frontend  | v2.418     | v2.417           | —             |
| social-api            | v2.418     | v2.417           | —             |
| social-worker         | v2.418     | v2.417           | —             |
| social-frontend       | v2.418     | v2.417           | latest        |

## CI workflow

File: `.github/workflows/docker-ci.yml`

- **Trigger**: push to `master`, version tags (`v*`), PRs to `master`.
- **Matrix**: 6 services built in parallel.
- **Tag generation**: `v2.${{ github.run_number }}` computed in a separate step
  to avoid metadata-action template interpolation issues.
- **Compression**: zstd (level 3) for both the pushed image and the local tar
  used for Trivy scanning.
- **Scanning**: Trivy scans the local tar (`/tmp/image.tar`) with `--input`
  instead of pulling from the registry.
- **Cache**: GitHub Actions cache (`type=gha`).
- **Provenance**: disabled to reduce metadata bloat.

## Checking tags on Docker Hub

```bash
# List tags for a service
curl -s "https://hub.docker.com/v2/repositories/baltzakist/cu130-slim-social-api/tags/?page_size=10" \
  | python3 -c "
import sys, json
for t in json.load(sys.stdin).get('results', []):
    print(f'{t[\"name\"]:20s}  {t[\"last_updated\"][:19]}')
"

# Or check all services at once
for svc in comfyui env-manager-backend env-manager-frontend social-api social-worker social-frontend; do
  echo \"=== \$svc ===\"
  curl -s \"https://hub.docker.com/v2/repositories/baltzakist/cu130-slim-\$svc/tags/?page_size=5\" \
    | python3 -c \"
import sys, json
for t in json.load(sys.stdin).get('results', []):
    print(f'  {t[\"name\"]:20s}  {t[\"last_updated\"][:19]}')
\"
done
```
