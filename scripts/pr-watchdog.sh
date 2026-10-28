#!/usr/bin/env bash
# pr-watchdog — heals stalled PR lifecycles on our protected repos.
# Modes observed 2026-10-28: all-checks-green but mergeable_state=blocked
# (unresolved bot review thread), behind-but-updatable, auto-merge armed
# but never firing.
#
# Policy: never auto-resolve review threads (a bot flag may be real) and
# never merge a PR that doesn't have autoMergeRequest armed — the human
# (or opening agent) declares merge intent; we only heal the mechanics.
#
# Hourly via scheduled task:
#   conhost.exe --headless "C:\Program Files\Git\bin\bash.exe" -lc '~/cu130-slim/scripts/pr-watchdog.sh'
set -u
cd "$(dirname "$0")/.."

REPOS="Themis128/cu130-slim Themis128/cloudless.gr"

TOKEN=$(printf 'protocol=https\nhost=github.com\n' | git credential fill 2>/dev/null | grep ^password= | cut -d= -f2)
if [ -z "$TOKEN" ]; then
  echo "[pr-watchdog] no github credential — skipping"
  exit 0
fi

ALERT() {  # Slack via social-api → raw webhook fallback; throttled 6h/key
  local key="$1" msg="$2" f="/tmp/pw-$(echo "$key" | md5sum | cut -c1-8)"
  [ -f "$f" ] && [ $(( $(date +%s) - $(stat -c %Y "$f" 2>/dev/null || echo 0) )) -lt 21600 ] && return
  docker compose exec -T social-api python -c "
import asyncio
from app.services.slack_notifications import post_alert_to_slack
asyncio.run(post_alert_to_slack('$msg'))" >/dev/null 2>&1 && touch "$f" && return
  [ -n "${SLACK_ALERTS_WEBHOOK_URL:-}" ] && curl -sf -X POST "$SLACK_ALERTS_WEBHOOK_URL" \
    -H 'Content-Type: application/json' -d "{\"text\":\"$msg\"}" >/dev/null 2>&1 && touch "$f" || true
}

api() { curl -sf -H "Authorization: token $TOKEN" -H "Accept: application/vnd.github+json" "$@"; }

gql() { curl -sf -H "Authorization: bearer $TOKEN" -X POST https://api.github.com/graphql -d "$1"; }

for repo in $REPOS; do
  prs=$(api "https://api.github.com/repos/$repo/pulls?state=open&per_page=30" \
        | python -c "import json,sys; [print(p['number'], p['head']['ref'], bool(p.get('auto_merge'))) for p in json.load(sys.stdin)]" 2>/dev/null) || continue
  [ -z "$prs" ] && continue
  while read -r num branch automerge; do
    pr=$(api "https://api.github.com/repos/$repo/pulls/$num") || continue
    state=$(echo "$pr" | python -c "import json,sys; print(json.load(sys.stdin).get('mergeable_state','unknown'))")
    # Required-checks verdict from combined status rollup
    checks=$(api "https://api.github.com/repos/$repo/commits/$branch/check-runs?per_page=50" \
      | python -c "import json,sys
runs=json.load(sys.stdin).get('check_runs',[])
bad=[r for r in runs if r['status']=='completed' and r.get('conclusion') in ('failure','cancelled','timed_out')]
pend=[r for r in runs if r['status']!='completed']
print('pending' if pend else ('fail' if bad else 'green'))" 2>/dev/null)
    [ -z "$checks" ] && checks="green"   # no check-runs yet (bot-PR approval gate)

    case "$checks" in
      fail) ALERT "pr-$num-fail" ":x: $repo#$num has FAILED checks — needs a fix, not a merge" ; continue;;
      pending) continue;;  # checks still running — nothing to heal
    esac

    # checks green below
    if [ "$automerge" = "True" ]; then
      case "$state" in
        clean)
          echo "[pr-watchdog] $repo#$num green+clean but auto-merge didn't fire — merging"
          api -X PUT "https://api.github.com/repos/$repo/pulls/$num/merge" -d '{"merge_method":"squash"}' >/dev/null \
            && ALERT "pr-$num-merged" ":white_check_mark: $repo#$num merged by pr-watchdog (auto-merge had stalled)" \
            || ALERT "pr-$num-mergefail" ":warning: $repo#$num merge call failed despite clean state" ;;
        behind)
          echo "[pr-watchdog] $repo#$num behind — requesting update-branch"
          api -X PUT "https://api.github.com/repos/$repo/pulls/$num/update-branch" >/dev/null 2>&1 || true ;;
        blocked)
          # Surface WHY it's blocked so a human can resolve the gate
          threads=$(gql "{\"query\":\"{ repository(owner:\\\"${repo%%/*}\\\", name:\\\"${repo##*/}\\\") { pullRequest(number:$num) { reviewThreads(first:20) { nodes { isResolved } } } } }\"}" \
            | python -c "import json,sys; print(sum(1 for t in json.load(sys.stdin)['data']['repository']['pullRequest']['reviewThreads']['nodes'] if not t['isResolved']))" 2>/dev/null)
          ALERT "pr-$num-blocked" ":warning: $repo#$num green but BLOCKED — ${threads:-?} unresolved review thread(s) or required review" ;;
      esac
    fi
  done <<< "$prs"
done
exit 0
