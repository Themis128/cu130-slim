#!/usr/bin/env bash
# Run a repo notebook inside social-jupyter (papermill, output → notebooks/output/).
# Usage: scripts/run-notebook.sh weekly-analytics.ipynb
# Cron: 0 8 * * 1 cd /home/tbaltzakis/cu130-slim && scripts/run-notebook.sh weekly-analytics.ipynb
set -eu
NB="${1:?usage: run-notebook.sh <file.ipynb>}"
OUT="output/$(basename "$NB" .ipynb)-$(date +%Y%m%d-%H%M).ipynb"
docker exec social-jupyter python -m papermill "/home/jovyan/work/$NB" "/home/jovyan/work/$OUT" --kernel python3
echo "wrote notebooks/$OUT"
