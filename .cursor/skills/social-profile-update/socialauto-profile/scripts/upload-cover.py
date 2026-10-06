#!/usr/bin/env python3
"""Upload a cover photo for a connected social account.
Usage: upload-cover.py <account-id> <image-file>"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import social_api, upload, usage  # noqa: E402

if len(sys.argv) < 3 or not Path(sys.argv[2]).is_file():
    usage("upload-cover.py <account-id> <image-file>")
api, token = social_api()
d = upload("POST", f"{api}/api/v1/profile/{sys.argv[1]}/cover",
           sys.argv[2], token=token)
print(json.dumps(d, indent=2, ensure_ascii=False))
