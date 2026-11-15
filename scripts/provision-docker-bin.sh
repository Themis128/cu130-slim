#!/usr/bin/env bash
# provision-docker-bin.sh — populate .docker-bin/ (gitignored) with the docker
# CLI + compose plugin that stack-watchers bind-mounts. Copies real files so the
# container never binds /mnt/wsl/docker-desktop/cli-tools directly (fsbridge
# flaps poison those binds into empty dirs → exit 127). Re-run after a Docker
# Desktop upgrade. Usage: scripts/provision-docker-bin.sh [repo-dir]
set -euo pipefail

REPO="${1:-$(cd "$(dirname "$0")/.." && pwd)}"
DD=/mnt/wsl/docker-desktop/cli-tools
DEST="$REPO/.docker-bin"

pick() {  # first existing regular file among the candidates
    for c in "$@"; do
        [ -n "$c" ] && [ -e "$c" ] && { readlink -f "$c"; return 0; }
    done
    return 1
}

docker_src=$(pick "$DD/usr/bin/docker" "$(command -v docker || true)") \
    || { echo "docker CLI not found" >&2; exit 1; }
compose_src=$(pick "$DD/usr/local/lib/docker/cli-plugins/docker-compose" \
    /usr/local/lib/docker/cli-plugins/docker-compose \
    /usr/libexec/docker/cli-plugins/docker-compose \
    "$HOME/.docker/cli-plugins/docker-compose") \
    || { echo "docker-compose plugin not found" >&2; exit 1; }

mkdir -p "$DEST/cli-plugins"
install -m 0755 "$docker_src" "$DEST/docker"
install -m 0755 "$compose_src" "$DEST/cli-plugins/docker-compose"

"$DEST/docker" --version
"$DEST/cli-plugins/docker-compose" version
echo "provisioned $DEST"
