#!/usr/bin/env python3
"""Upload a file to the media library.
Usage: upload-media.py <file-path> [--alt "description"] [--tags "tag1,tag2"]"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import social_api, upload  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("file")
p.add_argument("--alt", default="")
p.add_argument("--tags", default="")
a = p.parse_args()

api, token = social_api()
extra = {}
if a.alt:
    extra["alt_text"] = a.alt
if a.tags:
    extra["tags"] = a.tags
print(f"Uploading {a.file}...")
d = upload("POST", f"{api}/api/v1/media/upload", a.file, extra=extra, token=token)
print(f'Media ID: {d.get("id", "?")}')
print(f'Filename: {d.get("filename", "?")}')
print(f'MIME: {d.get("mime_type", "?")}')
print(f'Size: {d.get("size_bytes", 0)} bytes')
