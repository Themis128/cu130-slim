#!/usr/bin/env python3
"""Delete a media asset.
Usage: delete-media.py <media-id>"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import request, social_api, usage  # noqa: E402

media_id = sys.argv[1] if len(sys.argv) > 1 else usage("delete-media.py <media-id>")
api, token = social_api()
print(f"Deleting media {media_id}...")
request("DELETE", f"{api}/api/v1/media/{media_id}", token=token)
print("Deleted.")
