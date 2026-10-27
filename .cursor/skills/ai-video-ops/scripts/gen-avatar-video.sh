#!/usr/bin/env bash
# gen-avatar-video.sh <scenes.json> [filename.mp4] [team_id] [user_id]
#
# Submit an avatar story-video job to the media worker and wait for the
# media_assets row. scenes.json example:
#   [{"clip":"sad","line1":"A founder spent €40,000","line2":"on a website that did nothing.","duration":7},
#    {"clip":"happy","line1":"3x more leads","line2":"in 4 weeks.","duration":9}]
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

SCENES_FILE="${1:?usage: gen-avatar-video.sh <scenes.json> [filename.mp4]}"
FILENAME="${2:-avatar-story.mp4}"

docker compose exec -T social-worker-media python - "$SCENES_FILE" "$FILENAME" <<'PY'
import json, sys, time
from celery import current_app
scenes = json.load(open(sys.argv[1]) if sys.argv[1] != "-" else sys.stdin)
filename = sys.argv[2] if len(sys.argv) > 2 else "avatar-story.mp4"
res = current_app.send_task(
    "app.worker.tasks.avatar_video.generate_avatar_story",
    kwargs={"scenes": scenes, "filename": filename})
print(f"task {res.id} submitted — polling result...")
out = res.get(timeout=2700)
print(json.dumps(out, indent=1))
PY
