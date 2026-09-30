#!/usr/bin/env python3
"""Poll a SocialAuto post's TikTok publish status until complete or failed.
Usage: poll-status.py <post-id> [max-wait-seconds]"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import request, social_api, usage  # noqa: E402

post_id = sys.argv[1] if len(sys.argv) > 1 else \
    usage("poll-status.py <post-id> [max-wait-seconds]")
max_wait = int(sys.argv[2]) if len(sys.argv) > 2 else 180

api, token = social_api()

interval, elapsed = 10, 0
status = "unknown"
while elapsed < max_wait:
    d = request("GET", f"{api}/api/v1/content/posts/{post_id}", token=token)
    status = d.get("status", "?")
    targets = d.get("targets", d.get("post_targets", []))
    tgt = ",".join(f'{t.get("platform", "?")}:{t.get("status", "?")}'
                   for t in targets) if isinstance(targets, list) else str(targets)
    reason = d.get("error") or d.get("failure_reason") or ""
    print(f"[{elapsed}s] post={status} targets={tgt} reason={reason}")
    if status in ("published", "failed"):
        sys.exit(0)
    time.sleep(interval)
    elapsed += interval
print(f"Timeout after {max_wait}s — post is still {status}")
sys.exit(1)
