#!/usr/bin/env bash
# pr_resolve_threads.sh — resolve all unresolved review threads on a PR so
# merge can proceed (cloudless.gr enforces "all comments resolved").
#   pr_resolve_threads.sh <owner/repo> <pr-number>
set -e
REPO="${1:?owner/repo}"; PR="${2:?number}"; OWNER="${REPO%%/*}"; NAME="${REPO##*/}"
IDS=$(gh api graphql -f query="{repository(owner:\"$OWNER\",name:\"$NAME\"){pullRequest(number:$PR){reviewThreads(first:100){nodes{id isResolved}}}}}" \
      --jq '.data.repository.pullRequest.reviewThreads.nodes[]|select(.isResolved==false)|.id')
for id in $IDS; do
  gh api graphql -f query="mutation{resolveReviewThread(input:{threadId:\"$id\"}){thread{isResolved}}}" >/dev/null \
    && echo "resolved $id"
done
[ -z "$IDS" ] && echo "no unresolved threads"
