#!/bin/bash
# Build, tag, push all custom Docker images to GitHub Container Registry (GHCR)
# Updates docker-compose.yml to use the chosen tag.
#
# GHCR uses the built-in GITHUB_TOKEN / gh CLI for auth. Make packages public
# in the GitHub UI after first push so anonymous pulls (Trivy, compose) work.

set -euo pipefail

# Configuration
GHCR_REGISTRY="ghcr.io"
OWNER="${GITHUB_OWNER:-}"
if [ -z "$OWNER" ] && command -v gh >/dev/null 2>&1; then
    OWNER="$(gh repo view --json owner -q .owner.login 2>/dev/null | tr '[:upper:]' '[:lower:]' || true)"
fi
OWNER="${OWNER:-themis128}"
PROJECT_NAME="cu130-slim"
TAG="${TAG:-latest}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() { echo -e "${BLUE}[INFO]${NC} $*"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

# Check GHCR login via gh CLI or GITHUB_TOKEN
check_ghcr_login() {
    log_info "Checking GitHub Container Registry authentication..."
    if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
        log_success "Authenticated with gh CLI"
        return 0
    fi
    if [ -n "${GITHUB_TOKEN:-}" ]; then
        log_info "Logging into ghcr.io with GITHUB_TOKEN..."
        echo "$GITHUB_TOKEN" | docker login ghcr.io -u "$GITHUB_ACTOR" --password-stdin >/dev/null 2>&1 || true
    fi
    if docker info 2>/dev/null | grep -q "ghcr.io"; then
        log_success "Authenticated to ghcr.io"
        return 0
    fi
    log_warn "Not logged in to ghcr.io. Please run: gh auth login && gh auth token | docker login ghcr.io -u USERNAME --password-stdin"
    read -p "Login now with gh? (y/N) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        gh auth login
        gh auth token | docker login ghcr.io -u "$(gh api user -q .login)" --password-stdin
    else
        log_error "GHCR login required. Exiting."
        exit 1
    fi
}

# Build and push a single image
build_and_push() {
    local service_name=$1
    local dockerfile_path=$2
    local build_context=$3
    local image_name="${GHCR_REGISTRY}/${OWNER}/${PROJECT_NAME}:${service_name}-${TAG}"

    log_info "Building ${service_name}..."
    log_info "  Dockerfile: ${dockerfile_path}"
    log_info "  Context: ${build_context}"
    log_info "  Target image: ${image_name}"

    if docker build -f "${dockerfile_path}" -t "${image_name}" "${build_context}"; then
        log_success "Built ${image_name}"
    else
        log_error "Failed to build ${service_name}"
        return 1
    fi

    log_info "Pushing ${image_name} to GHCR..."
    if docker push "${image_name}"; then
        log_success "Pushed ${image_name}"
    else
        log_error "Failed to push ${service_name}"
        return 1
    fi

    # Also tag as latest explicitly
    docker tag "${image_name}" "${GHCR_REGISTRY}/${OWNER}/${PROJECT_NAME}:${service_name}-latest"
    docker push "${GHCR_REGISTRY}/${OWNER}/${PROJECT_NAME}:${service_name}-latest"
    log_success "Tagged and pushed :latest for ${service_name}"
}

# Update docker-compose.yml with new image references
update_docker_compose() {
    local compose_file="docker-compose.yml"
    local backup_file="docker-compose.yml.backup.$(date +%Y%m%d_%H%M%S)"

    log_info "Backing up current docker-compose.yml to ${backup_file}"
    cp "${compose_file}" "${backup_file}"

    log_info "Updating docker-compose.yml with ${GHCR_REGISTRY}/${OWNER}/${PROJECT_NAME}:*-${TAG} images..."

    local prefix="${GHCR_REGISTRY}/${OWNER}/${PROJECT_NAME}"

    # Replace image lines for custom-built services. Use a robust sed that
    # targets the whole file rather than per-service blocks, which avoids
    # mangling build contexts when services use inline build definitions.
    sed -i "s|image: .*cu130-slim[:\-]comfyui[^ ]*|image: ${prefix}:comfyui-${TAG}|" "${compose_file}"
    sed -i "s|image: .*cu130-slim[:\-]env-manager-backend[^ ]*|image: ${prefix}:env-manager-backend-${TAG}|" "${compose_file}"
    sed -i "s|image: .*cu130-slim[:\-]env-manager-frontend[^ ]*|image: ${prefix}:env-manager-frontend-${TAG}|" "${compose_file}"
    sed -i "s|image: .*cu130-slim[:\-]social-api[^ ]*|image: ${prefix}:social-api-${TAG}|" "${compose_file}"
    sed -i "s|image: .*cu130-slim[:\-]social-worker[^ ]*|image: ${prefix}:social-worker-${TAG}|" "${compose_file}"
    sed -i "s|image: .*cu130-slim[:\-]social-frontend[^ ]*|image: ${prefix}:social-frontend-${TAG}|" "${compose_file}"

    log_success "Updated docker-compose.yml"
    log_info "Backup saved as: ${backup_file}"
}

# Main execution
main() {
    log_info "Starting build, tag, and push for all custom images"
    log_info "GHCR namespace: ${GHCR_REGISTRY}/${OWNER}"
    log_info "Project: ${PROJECT_NAME}"
    log_info "Tag: ${TAG}"
    echo

    check_ghcr_login

    # Array of services to build: "service_name|dockerfile_path|build_context"
    declare -a services=(
        "comfyui|Dockerfile|."
        "env-manager-backend|env-manager/backend/Dockerfile|env-manager/backend"
        "env-manager-frontend|env-manager/frontend/Dockerfile|env-manager/frontend"
        "social-api|social-automation/backend/Dockerfile|social-automation/backend"
        "social-worker|social-automation/backend/Dockerfile.worker|social-automation/backend"
        "social-frontend|social-automation/frontend/Dockerfile|social-automation/frontend"
    )

    # Build and push each service
    failed=()
    for service_def in "${services[@]}"; do
        IFS='|' read -r name dockerfile context <<< "${service_def}"
        if ! build_and_push "${name}" "${dockerfile}" "${context}"; then
            failed+=("${name}")
        fi
        echo
    done

    # Update docker-compose.yml
    update_docker_compose

    # Summary
    echo
    log_info "=== BUILD SUMMARY ==="
    if [[ ${#failed[@]} -eq 0 ]]; then
        log_success "All images built and pushed successfully!"
        log_info "Images pushed:"
        for service_def in "${services[@]}"; do
            IFS='|' read -r name _ _ <<< "${service_def}"
            echo "  ${GHCR_REGISTRY}/${OWNER}/${PROJECT_NAME}:${name}-${TAG}"
        done
        log_info "Make the single package public at: https://github.com/${OWNER}?tab=packages"
    else
        log_error "Failed services: ${failed[*]}"
        exit 1
    fi
}

main "$@"
