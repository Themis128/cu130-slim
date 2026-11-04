#!/bin/bash
# retry-push.sh [remote] [branch] — push until ls-remote confirms
remote=${1:-origin}; br=${2:-$(git branch --show-current)}
for i in 1 2 3 4 5; do
  timeout 55 git push -u "$remote" "$br" &>/dev/null
  local=$(git rev-parse HEAD); rem=$(git ls-remote "$remote" "$br" | cut -f1)
  [ "$local" = "$rem" ] && { echo "PUSHED $br@$local"; exit 0; }
  echo "retry $i"; sleep 15
done
echo "FAILED — vsock still flapping; try push-via-windows.ps1"; exit 1
