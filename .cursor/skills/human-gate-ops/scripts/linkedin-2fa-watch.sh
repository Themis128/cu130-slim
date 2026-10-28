#!/usr/bin/env bash
# Watch the linkedin-sidecar session for the pending_2fa checkpoint to
# clear; alert the ops channel on state change or after NUDGE_HOURS.
set -u
SIDECAR="${LINKEDIN_SIDECAR_URL:-http://localhost:9222}"
NUDGE_HOURS="${NUDGE_HOURS:-6}"
last=""; notified=0
post() {  # $1 = text
  cd /home/tbaltzakis/cu130-slim && docker compose exec -T social-api \
    python -c "import asyncio; from app.core.config import get_settings; from app.services.slack_notifications import _post_slack_text,_get_slack_token; s=get_settings(); asyncio.run(_post_slack_text(text='''$1''',webhook_url='',token=_get_slack_token(),channel_id=s.SLACK_CHANNEL_ID,purpose='2fa-watch'))" >/dev/null 2>&1
}
while :; do
  state=$(curl -fs --max-time 10 "$SIDECAR/session" 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('status') or d.get('checkpoint') or 'unknown')" 2>/dev/null || echo "sidecar-unreachable")
  if [ "$state" != "$last" ]; then
    case "$state" in
      *pending_2fa*|*checkpoint*) post ":warning: linkedin-sidecar still waiting on 2FA — approve the push in the LinkedIn app (#794)"; notified=$(date +%s);;
      *logged_in*|*ok*|*active*) post ":white_check_mark: linkedin-sidecar session healthy — 2FA cleared, outreach can resume (#794)";;
      *) echo "$(date -Is) state=$state";;
    esac
    last="$state"
  fi
  # re-nudge if still pending after NUDGE_HOURS
  if [[ "$state" == *pending_2fa* ]] && [ -n "$notified" ] && [ $(( $(date +%s) - notified )) -gt $((NUDGE_HOURS*3600)) ]; then
    post ":warning: linkedin 2FA still pending after ${NUDGE_HOURS}h — approve in the app or the prompt expires (#794)"
    notified=$(date +%s)
  fi
  sleep 60
done
