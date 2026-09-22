# Docker Image Registry

CI-built images are hosted on **GitHub Container Registry (GHCR)** under the
`ghcr.io/themis128` namespace (public option). CI tags images from
`docker-compose.yml` and pushes via `.github/workflows/build-and-push.yml`.

> **Legacy**: Older builds may still exist on Docker Hub under `baltzakist/`.
> New pushes go to GHCR. Make GHCR packages **public** in the GitHub UI
> (Packages → package → Package settings → Change visibility) so anonymous
> `docker pull` and unauthenticated Trivy remote pulls succeed.

## Image naming convention

```
ghcr.io/themis128/cu130-slim-<service>:<tag>
```

## Tag types

| Tag format        | Description                                      | Updated by         |
|-------------------|--------------------------------------------------|--------------------|
| `latest`          | Moving alias when published                      | CI / manual        |
| `v2.<run_number>` | Version tag pinned in `docker-compose.yml`       | Compose + CI       |

## CI-built services

| Service               | GHCR repository                                      | Compose service         |
|-----------------------|------------------------------------------------------|-------------------------|
| ComfyUI               | `ghcr.io/themis128/cu130-slim-comfyui`               | `comfyui`               |
| Env Manager Backend   | `ghcr.io/themis128/cu130-slim-env-manager-backend`   | `env-manager-backend`   |
| Env Manager Frontend  | `ghcr.io/themis128/cu130-slim-env-manager-frontend`  | `env-manager-frontend`  |
| Social API            | `ghcr.io/themis128/cu130-slim-social-api`            | `social-api`            |
| Social Worker         | `ghcr.io/themis128/cu130-slim-social-worker`         | `social-worker-*` / `celery-beat` |
| Social Frontend       | `ghcr.io/themis128/cu130-slim-social-frontend`       | `social-frontend`       |
| cloudflared           | `ghcr.io/themis128/cu130-slim-cloudflared`           | `cloudflared`           |

`celery-beat` and the `social-worker-*` services share `cu130-slim-social-worker`.

## cloudflared

Previously published only to Docker Hub as `baltzakist/cloudflared:v0.2`.
Compose now uses GHCR. Build locally or via CI:

```bash
docker compose --env-file .env build cloudflared
docker push ghcr.io/themis128/cu130-slim-cloudflared:v0.2
```

Until that tag exists on GHCR, keep a local copy or temporarily pull the Hub
image and retag.

## How Compose consumes the images

Pinned version tags in `docker-compose.yml`, e.g.:

```yaml
image: ghcr.io/themis128/cu130-slim-social-api:v2.419
```

## CI workflows

| Workflow | Role |
|----------|------|
| `.github/workflows/build-and-push.yml` | Build + push to **GHCR** (`packages: write`) |
| `.github/workflows/docker-compose-validation.yml` | Assert compose image prefixes are `ghcr.io/themis128/` (includes `cloudflared`) |
| `.github/workflows/trivy-scan.yml` / `security.yml` | Login to GHCR, pull compose ref (or build), scan |
| `.github/workflows/docker-ci.yml` | Secondary GHCR build path (`packages: write`) — prefer `build-and-push.yml` |

## Making packages public (manual)

1. Open https://github.com/Themis128?tab=packages
2. For each `cu130-slim-*` package → Package settings → Change visibility → Public
3. Re-run Trivy / Security Scanning / Build and push as needed

## Manual release

`scripts/build-tag-push-all.sh` builds, tags, and pushes all images to GHCR
(`ghcr.io/<owner>/cu130-slim-<service>:<tag>`) and rewrites compose refs to the
same namespace. Auth via `gh auth token | docker login ghcr.io -u <user>
--password-stdin` — no Docker Hub credentials needed.

## Checking tags on GHCR

```bash
# Requires gh auth with read:packages (or public packages)
gh api user/packages?package_type=container --jq '.[].name'
```
