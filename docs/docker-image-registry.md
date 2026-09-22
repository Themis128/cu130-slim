# Docker Image Registry

CI-built images are hosted on **GitHub Container Registry (GHCR)** under a
**single package**:

```
ghcr.io/themis128/cu130-slim:<service>-<tag>
```

CI tags images from `docker-compose.yml` and pushes via
`.github/workflows/build-and-push.yml`.

> **Legacy**: Older builds may still exist as separate packages
> (`ghcr.io/themis128/cu130-slim-<service>`) or on Docker Hub under
> `baltzakist/`. New pushes go only to the single `cu130-slim` package.
> Make that package **public** in the GitHub UI (Packages → cu130-slim →
> Package settings → Change visibility) so anonymous `docker pull` and
> unauthenticated Trivy remote pulls succeed.

## Image naming convention

```
ghcr.io/themis128/cu130-slim:<service>-<tag>
```

Examples: `social-api-latest`, `social-worker-sha-abc1234`,
`social-worker-publishing-latest`, `comfyui-latest`.

## Tag types

| Tag format | Description | Updated by |
|------------|-------------|------------|
| `<service>-latest` | Moving alias for the newest successful build of that service | Build and push CI |
| `<service>-sha-<7-char-sha>` | Immutable per-commit tag kept on GHCR for rollbacks | Build and push CI |
| `v2.*` / `v0.*` (legacy multi-package) | Historical pins on old per-service packages | Historical |

## CI-built services

| Service | GHCR tag prefix | Compose service |
|---------|-----------------|-----------------|
| ComfyUI | `comfyui-` | `comfyui` |
| Env Manager Backend | `env-manager-backend-` | `env-manager-backend` |
| Env Manager Frontend | `env-manager-frontend-` | `env-manager-frontend` |
| Social API | `social-api-` | `social-api` |
| Social Worker | `social-worker-` | `social-worker-*` / `celery-beat` |
| Social Worker (publishing build) | `social-worker-publishing-` | `social-worker-publishing` |
| Social Frontend | `social-frontend-` | `social-frontend` |
| cloudflared | `cloudflared-` | `cloudflared` |

`celery-beat`, `social-worker-media`, `social-worker-default`, and
`social-worker-messenger` share `social-worker-*` tags. The publishing worker
uses `social-worker-publishing-*` (same Dockerfile; CI also publishes the
shared `social-worker-*` tags from that job).

## cloudflared

Previously published only to Docker Hub as `baltzakist/cloudflared:v0.2`
(**legacy**). Compose now uses the single GHCR package. Build locally or via CI:

```bash
docker compose --env-file .env build cloudflared
docker tag <built> ghcr.io/themis128/cu130-slim:cloudflared-latest
docker push ghcr.io/themis128/cu130-slim:cloudflared-latest
```

## How Compose consumes the images

`docker-compose.yml` references moving `<service>-latest` tags and sets
`pull_policy: always` on every first-party service, e.g.:

```yaml
image: ghcr.io/themis128/cu130-slim:social-api-latest
pull_policy: always
```

Every `docker compose up -d`/`create` checks the registry digest and pulls a
new build when one exists — no manual pin updates needed. `docker compose
restart` does **not** pull; use `docker compose up -d <service>` to pick up a
fresh image. A failed pull aborts `up`, so this requires registry
reachability (the package should be public — anonymous pull works).

`docker-compose.override.yml` (local only) may override individual services
for iteration. CI validation requires the single-package `<service>-latest`
refs and `pull_policy: always` in `docker-compose.yml`.

## Tags published by CI

The **Build and push images** workflow (`.github/workflows/build-and-push.yml`)
publishes each app image twice on every successful run into **one** package:

| Tag | Meaning |
|-----|---------|
| `<service>-latest` | Moving alias consumed by compose (`pull_policy: always`) |
| `<service>-sha-<7-char-sha>` | Immutable pin for the commit that was built — kept for rollbacks |

Third-party images (n8n, postgres, redis, etc.) are never touched.

### Rollback

To roll a service back to a specific commit build, pin its `image:` line to
`ghcr.io/themis128/cu130-slim:<service>-sha-<sha>` locally (or commit it) and
`docker compose up -d <service>`. Helper script for bulk re-pinning:
`scripts/update-compose-image-tags.sh <tag-suffix>`.

### Loop prevention

`docker-compose.yml` is **not** in the workflow `push.paths` filters (only
Dockerfiles and the workflow file itself trigger rebuilds, plus
`workflow_dispatch`), so compose changes never retrigger a full image build.

## CI workflows

| Workflow | Role |
|----------|------|
| `.github/workflows/build-and-push.yml` | Build + push to **single GHCR package** (`packages: write`) |
| `.github/workflows/docker-compose-validation.yml` | Assert compose image refs are `ghcr.io/themis128/cu130-slim:<service>-latest` |
| `.github/workflows/trivy-scan.yml` / `security.yml` | Login to GHCR, pull compose ref (or build), scan |
| `.github/workflows/docker-ci.yml` | Secondary GHCR build path (`packages: write`) — prefer `build-and-push.yml` |

## Making the package public (manual)

1. Open https://github.com/users/Themis128/packages/container/package/cu130-slim
2. Package settings → Change visibility → Public
3. Re-run Trivy / Security Scanning / Build and push as needed

Legacy per-service packages (`cu130-slim-*`) can stay public for old pins but
are no longer updated.

## Manual release

`scripts/build-tag-push-all.sh` builds, tags, and pushes all images to the
single GHCR package (`ghcr.io/<owner>/cu130-slim:<service>-<tag>`) and rewrites
compose refs. Auth via `gh auth token | docker login ghcr.io -u <user>
--password-stdin` — no Docker Hub credentials needed for app images.

## Checking tags on GHCR

```bash
# Requires gh auth with read:packages (or public package)
gh api user/packages/container/cu130-slim/versions --jq '.[].metadata.container.tags'
```
