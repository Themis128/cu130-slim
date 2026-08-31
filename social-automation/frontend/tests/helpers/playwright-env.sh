#!/bin/sh
# Playwright browser dependency setup for user-space (no sudo) environments.
#
# Chromium and Firefox work with the extracted .deb libraries below.
# WebKit is skipped (needs libgstplay-1.0 which isn't available without sudo
# on this Ubuntu version).
#
# Source this file before running playwright:
#   . ./tests/helpers/playwright-env.sh
#   ./node_modules/.bin/playwright test --project=chromium

DEPS_DIR="/home/tbaltzakis/.local/lib/playwright-deps"
LIB_DIRS=""
for d in \
  "$DEPS_DIR/usr/lib/x86_64-linux-gnu" \
  "$DEPS_DIR/lib/x86_64-linux-gnu" \
  "$DEPS_DIR/usr/lib/x86_64-linux-gnu/gstreamer-1.0"; do
  if [ -d "$d" ]; then
    if [ -z "$LIB_DIRS" ]; then LIB_DIRS="$d"; else LIB_DIRS="$LIB_DIRS:$d"; fi
  fi
done

export LD_LIBRARY_PATH="${LIB_DIRS}:${LD_LIBRARY_PATH}"
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-/home/tbaltzakis/.cache/ms-playwright}"
