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

| Tag format         | Description                                      | Updated by         |
|--------------------|--------------------------------------------------|--------------------|
| `latest`           | Moving alias for the newest successful build     | Build and push CI  |
| `sha-<7-char-sha>` | Immutable pin written into `docker-compose.yml`  | Build and push CI  |
| `v2.*` / `v0.*`    | Legacy pins (pre auto-update); still pullable    | Historical         |

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

`docker-compose.yml` references the moving `latest` tag and sets
`pull_policy: always` on every `cu130-slim-*` service, e.g.:

```yaml
image: ghcr.io/themis128/cu130-slim-social-api:latest
pull_policy: always
```

Every `docker compose up -d`/`create` checks the registry digest and pulls a
new build when one exists — no manual pin updates needed. `docker compose
restart` does **not** pull; use `docker compose up -d <service>` to pick up a
fresh image. A failed pull aborts `up`, so this requires registry
reachability (the packages are public — anonymous pull works).

`docker-compose.override.yml` (local only) may override individual services
for iteration. CI validation requires the GHCR `:latest` refs and
`pull_policy: always` in `docker-compose.yml`.

## Tags published by CI

The **Build and push images** workflow (`.github/workflows/build-and-push.yml`)
publishes each app image twice on every successful run:

| Tag | Meaning |
|-----|---------|
| `latest` | Moving alias consumed by compose (`pull_policy: always`) |
| `sha-<7-char-sha>` | Immutable pin for the commit that was built — kept for rollbacks |

Third-party images (n8n, postgres, redis, etc.) are never touched.

### Rollback

To roll a service back to a specific commit build, pin its `image:` line to
`ghcr.io/themis128/cu130-slim-<service>:sha-<sha>` locally (or commit it) and
`docker compose up -d <service>`. Helper script for bulk re-pinning:
`scripts/update-compose-image-tags.sh <tag>`.

### Loop prevention

`docker-compose.yml` is **not** in the workflow `push.paths` filters (only
Dockerfiles and the workflow file itself trigger rebuilds, plus
`workflow_dispatch`), so compose changes never retrigger a full image build.

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
