#!/bin/bash
# Build, tag, push all custom Docker images to Docker Hub
# Updates docker-compose.yml to use :latest tags

set -euo pipefail

# Configuration
DOCKERHUB_USER="${DOCKERHUB_USER:-baltzakist}"
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

# Check Docker Hub login
check_docker_login() {
    log_info "Checking Docker Hub authentication..."
    if ! docker info 2>/dev/null | grep -q "Username: ${DOCKERHUB_USER}"; then
        log_warn "Not logged in as ${DOCKERHUB_USER}. Please run: docker login"
        read -p "Login now? (y/N) " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            docker login
        else
            log_error "Docker Hub login required. Exiting."
            exit 1
        fi
    fi
    log_success "Authenticated as ${DOCKERHUB_USER}"
}

# Build and push a single image
build_and_push() {
    local service_name=$1
    local dockerfile_path=$2
    local build_context=$3
    local image_name="${DOCKERHUB_USER}/${PROJECT_NAME}-${service_name}:${TAG}"
    
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
    
    log_info "Pushing ${image_name} to Docker Hub..."
    if docker push "${image_name}"; then
        log_success "Pushed ${image_name}"
    else
        log_error "Failed to push ${service_name}"
        return 1
    fi
    
    # Also tag as latest explicitly
    docker tag "${image_name}" "${DOCKERHUB_USER}/${PROJECT_NAME}-${service_name}:latest"
    docker push "${DOCKERHUB_USER}/${PROJECT_NAME}-${service_name}:latest"
    log_success "Tagged and pushed :latest for ${service_name}"
}

# Update docker-compose.yml with new image references
update_docker_compose() {
    local compose_file="docker-compose.yml"
    local backup_file="docker-compose.yml.backup.$(date +%Y%m%d_%H%M%S)"
    
    log_info "Backing up current docker-compose.yml to ${backup_file}"
    cp "${compose_file}" "${backup_file}"
    
    log_info "Updating docker-compose.yml with ${DOCKERHUB_USER}/${PROJECT_NAME}-*:${TAG} images..."
    
    # Use sed to replace image lines for custom-built services
    # comfyui
    sed -i "s|image: baltzakist/cu130-slim-comfyui:.*|image: ${DOCKERHUB_USER}/${PROJECT_NAME}-comfyui:${TAG}|" "${compose_file}"
    
    # env-manager-backend (build: context -> image)
    sed -i '/env-manager-backend:/,/^[[:space:]]*[a-z]/{
        /build:/,/^[[:space:]]*[a-z]/{
            /context:/d
            /dockerfile:/d
        }
        /build:/a\    image: '"${DOCKERHUB_USER}/${PROJECT_NAME}-env-manager-backend:${TAG}"'
    }' "${compose_file}"
    
    # env-manager-frontend
    sed -i '/env-manager-frontend:/,/^[[:space:]]*[a-z]/{
        /build:/,/^[[:space:]]*[a-z]/{
            /context:/d
            /dockerfile:/d
        }
        /build:/a\    image: '"${DOCKERHUB_USER}/${PROJECT_NAME}-env-manager-frontend:${TAG}"'
    }' "${compose_file}"
    
    # social-api
    sed -i '/social-api:/,/^[[:space:]]*[a-z]/{
        /build:/,/^[[:space:]]*[a-z]/{
            /context:/d
            /dockerfile:/d
        }
        /build:/a\    image: '"${DOCKERHUB_USER}/${PROJECT_NAME}-social-api:${TAG}"'
    }' "${compose_file}"
    
    # social-worker
    sed -i '/social-worker:/,/^[[:space:]]*[a-z]/{
        /build:/,/^[[:space:]]*[a-z]/{
            /context:/d
            /dockerfile:/d
        }
        /build:/a\    image: '"${DOCKERHUB_USER}/${PROJECT_NAME}-social-worker:${TAG}"'
    }' "${compose_file}"
    
    # social-frontend
    sed -i '/social-frontend:/,/^[[:space:]]*[a-z]/{
        /build:/,/^[[:space:]]*[a-z]/{
            /context:/d
            /dockerfile:/d
        }
        /build:/a\    image: '"${DOCKERHUB_USER}/${PROJECT_NAME}-social-frontend:${TAG}"'
    }' "${compose_file}"
    
    log_success "Updated docker-compose.yml"
    log_info "Backup saved as: ${backup_file}"
}

# Main execution
main() {
    log_info "Starting build, tag, and push for all custom images"
    log_info "Docker Hub user: ${DOCKERHUB_USER}"
    log_info "Project: ${PROJECT_NAME}"
    log_info "Tag: ${TAG}"
    echo
    
    check_docker_login
    
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
            log_info "  ${DOCKERHUB_USER}/${PROJECT_NAME}-${name}:${TAG}"
        done
        log_info ""
        log_info "docker-compose.yml updated to use :${TAG} tags"
        log_info "Run 'docker compose pull' on target machines to update"
    else
        log_error "Failed to build/push: ${failed[*]}"
        exit 1
    fi
}

# Run main
main "$@"