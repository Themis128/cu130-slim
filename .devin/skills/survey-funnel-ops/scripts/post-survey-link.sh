#!/usr/bin/env bash
# post-survey-link.sh <platform> — queue a survey post via SocialAuto.
# platforms: linkedin, threads, instagram, x, facebook
set -eu
cd "$(dirname "$0")/../../../.."
PLATFORM="${1:?usage: post-survey-link.sh <platform>}"
curl -s -m10 -X POST http://localhost:8083/api/v1/content \
  -H "Content-Type: application/json" \
  -d "{
    \"content\": \"Where does your stack actually run? Take the 60-second stack audit → https://cloudless.gr/survey\",
    \"platform\": \"$PLATFORM\",
    \"scheduled_for\": null
  }" | head -c 200
