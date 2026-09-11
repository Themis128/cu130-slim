#!/usr/bin/env bash
# Print the Meta "Report a Problem" description template.
# Usage: bash print-report-template.sh
set -euo pipefail

cat <<'EOF'
I am unable to complete business verification for my Meta Developer app
(Cloudless, App ID: 1936126137016578). When I go to App Review > Verification
and click Start verification for my business portfolio cloudless.gr, I get
a dialog saying "Your account is restricted right now. You have been
temporarily blocked from performing this action."

My personal ad account (ID: 657781691826702) has been disabled since
Jan 24, 2021. Meta says "too much time has passed" and the decision cannot
be reviewed through Account Quality.

However, I need business verification to complete App Review for my
Cloudless app. I have completed all other App Review requirements (app
icon, privacy policy, permissions, screencast, data handling, reviewer
instructions). The only blocker is this verification step.

Please help lift this restriction so I can verify my business and submit
for review.
EOF
