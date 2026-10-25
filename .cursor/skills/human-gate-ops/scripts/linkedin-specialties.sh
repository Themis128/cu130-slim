#!/usr/bin/env bash
# Last automated try at LinkedIn page specialties via the HEADED profile
# (browser-novnc persistent session). Keyboard-first entry, screenshots
# before/after to the ops channel. If it fails — the human does the
# 60-second manual edit; rerun after LinkedIn changes their UI.
set -u
cd /home/tbaltzakis/cu130-slim
echo "Drives browser-novnc session via Playwright MCP:"
cat <<'STEPS'
  1. browser_navigate → https://www.linkedin.com/company/…/edit/ (company id from linkedin-graph-ops)
  2. browser_click/keyboard: Tab to Specialties pill, type each of:
     Web Development · Managed Hosting · AI Automation · Custom Software
  3. browser_screenshot before + after → post to ops channel
  Keep Digital Marketing. If the pill input is display:none again — stop;
  16 headless paths already proved dead.
STEPS
