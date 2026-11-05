#!/usr/bin/env bash
# wsl-move-project.sh — copy a Windows-side project dir into the WSL distro,
# normalize the git checkout (NTFS->ext4 artifacts), and optionally rebind
# docker containers to the new path. Run INSIDE the distro:
#   bash wsl-move-project.sh /mnt/c/Users/<u>/<dir> [/home/<u>/<name>] [--compose-up]
set -euo pipefail
SRC="${1:?usage: wsl-move-project.sh <src-win-path> [dst] [--compose-up]}"
DST="${2:-/home/$USER/$(basename "$SRC")}"
COMPOSE_UP=0; [ "${3:-}" = "--compose-up" ] && COMPOSE_UP=1

[ -d "$SRC" ] || { echo "src missing: $SRC"; exit 1; }
mkdir -p "$(dirname "$DST")"
echo "[1/4] tar-copy $SRC -> $DST (9p is ~2.5MB/s; big trees take a while)"
( cd "$(dirname "$SRC")" && tar -cf - "$(basename "$SRC")" ) | tar -C "$(dirname "$DST")" -xf -

cd "$DST"
if [ -d .git ]; then
  echo "[2/4] normalize git checkout"
  git config core.fileMode false
  git checkout -- .
  echo "  status: $(git status --porcelain | wc -l) changes, $(git stash list | wc -l) stashes"
else
  echo "[2/4] no .git — skipping normalize"
fi

if [ "$COMPOSE_UP" = 1 ] && [ -f docker-compose.yml ]; then
  echo "[3/4] compose up -d (deploy-lock guarded)"
  echo "$(date -u +%FT%TZ) $0 migrate" > .deploy.lock
  docker compose up -d || true
  rm -f .deploy.lock
else
  echo "[3/4] compose skipped"
fi

echo "[4/4] done. Follow-ups:"
echo "  - retire Windows copy: rename to $(basename "$SRC").moved-to-wsl-$(date +%Y%m%d) (watchers may hold it — defer to IDE exit if busy)"
echo "  - repoint scheduled tasks to \\\\wsl.localhost\\<distro>$DST or wsl -d <distro> -e ..."
echo "  - check 'docker ps' for port collisions before blaming compose"
