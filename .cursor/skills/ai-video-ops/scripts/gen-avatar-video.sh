#!/usr/bin/env bash
# gen-avatar-video.sh <scenes.json|-> [filename.mp4] [team_id] [user_id] [music_url]
#
# Submit an avatar story-video job to the media worker and wait for the
# media_assets row. scenes.json example:
#   [{"clip":"sad","line1":"A founder spent €40,000","line2":"on a website that did nothing.","duration":7},
#    {"clip":"happy","line1":"3x more leads","line2":"in 4 weeks.","duration":9}]
#
# Without music_url the output is SILENT and must not be published
# (attach-audio rule) — add a track first.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

SCENES_FILE="${1:?usage: gen-avatar-video.sh <scenes.json|-> [filename.mp4] [team_id] [user_id] [music_url]}"
FILENAME="${2:-avatar-story.mp4}"
TEAM_ID="${3:-}"
USER_ID="${4:-}"
MUSIC_URL="${5:-}"

# The worker can't see host paths: ship the JSON itself via env.
if [ "$SCENES_FILE" = "-" ]; then SCENES_JSON="$(cat)"; else SCENES_JSON="$(cat "$SCENES_FILE")"; fi

docker compose exec -T \
  -e SCENES_JSON="$SCENES_JSON" -e FILENAME="$FILENAME" \
  -e TEAM_ID="$TEAM_ID" -e USER_ID="$USER_ID" -e MUSIC_URL="$MUSIC_URL" \
  social-worker-media python - <<'PY'
import json, os
from app.worker.celery_app import celery_app
kwargs = {"scenes": json.loads(os.environ["SCENES_JSON"]),
          "filename": os.environ.get("FILENAME") or "avatar-story.mp4"}
for env, key in (("TEAM_ID", "team_id"), ("USER_ID", "user_id"), ("MUSIC_URL", "music_url")):
    if os.environ.get(env):
        kwargs[key] = os.environ[env]
if "music_url" not in kwargs:
    print("WARNING: no music_url — output will be silent; do not publish it as-is")
res = celery_app.send_task("app.worker.tasks.avatar_video.generate_avatar_story", kwargs=kwargs)
print(f"task {res.id} submitted — polling result...")
print(json.dumps(res.get(timeout=2700), indent=1))
PY
