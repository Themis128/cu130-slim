#!/usr/bin/env python3
"""End-to-end social post pipeline: file -> correct media -> upload -> post -> publish.

Automates the manual chain used for the BMB certificate post (2026-11-03):
any source file is converted to the right media type, uploaded to the media
library, attached to a post, and published — then status is polled until the
platform reports a result.

Runs INSIDE social-api (converters + env creds live there):

    python /app/scripts/social_post.py \
        --file /app/pipeline-assets/bmb-cert.pdf \
        --caption "Done with Business Mastery Bootcamp 2.0..." \
        --account 18d5cd59-f0c2-4fc4-986e-03601734c7a5 \
        --sound --publish

Rules baked in (from repo product defaults):
  - video posts always carry audio (--sound synthesizes a license-free bed)
  - PDF -> PNG (docs render better than the doc viewer for single pages)
  - never publishes without media or while conversion produced nothing
  - platform media rules still apply server-side (e.g. LinkedIn video goes
    via the API path — the sidecar declines non-image media automatically)
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import uuid

API = os.environ.get("SOCIAL_API_BASE", "http://localhost:8000")
SCRIPTS = os.path.dirname(os.path.abspath(__file__))


def _req(method: str, path: str, token: str | None = None, data: dict | None = None,
         raw: bytes | None = None, headers: dict | None = None) -> dict:
    url = API + path
    hdrs = dict(headers or {})
    body = raw
    if data is not None:
        body = json.dumps(data).encode()
        hdrs["Content-Type"] = "application/json"
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} {path} -> {e.code}: {e.read()[:300]!r}")


def login() -> str:
    r = _req("POST", "/api/v1/auth/login", data={
        "email": os.environ["SOCIAL_ADMIN_EMAIL"],
        "password": os.environ["SOCIAL_ADMIN_PASSWORD"],
    })
    return r["access_token"]


def upload_media(token: str, path: str, alt: str = "") -> str:
    boundary = uuid.uuid4().hex
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    name = os.path.basename(path)
    parts = []
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                 f'filename="{name}"\r\nContent-Type: {mime}\r\n\r\n'.encode())
    parts.append(open(path, "rb").read())
    parts.append(f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"alt_text\"\r\n\r\n{alt}\r\n"
                 f"--{boundary}--\r\n".encode())
    r = _req("POST", "/api/v1/media/upload", token=token,
             raw=b"".join(parts),
             headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    return r["id"]


def convert(src: str, want_sound: bool, tmpdir: str) -> str:
    """Return the post-ready media path. Raises on conversion failure."""
    ext = os.path.splitext(src)[1].lower()
    if ext == ".pdf":
        img = os.path.join(tmpdir, "page1.png")
        subprocess.run([sys.executable, os.path.join(SCRIPTS, "pdf_to_image.py"), src, img],
                       check=True)
    elif ext in (".png", ".jpg", ".jpeg", ".webp"):
        img = src
    elif ext in (".mp4", ".mov", ".webm", ".mkv"):
        return src  # already video — trust the caller's file
    else:
        raise SystemExit(f"unsupported source type: {ext}")

    if want_sound or ext == ".pdf":
        # Video + audio beats a silent still for feed posts.
        wav = os.path.join(tmpdir, "bed.wav")
        subprocess.run([sys.executable, os.path.join(SCRIPTS, "audio_bed.py"),
                        wav, "--seconds", "6"], check=True)
        mp4 = os.path.join(tmpdir, "post.mp4")
        subprocess.run([sys.executable, os.path.join(SCRIPTS, "image_to_video.py"),
                        img, mp4, "--audio", wav, "--seconds", "6"], check=True)
        return mp4
    return img


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", required=True)
    ap.add_argument("--caption", required=True)
    ap.add_argument("--account", required=True, action="append", help="target social account UUID (repeatable)")
    ap.add_argument("--sound", action="store_true", help="force video+audio output for stills")
    ap.add_argument("--alt", default="")
    ap.add_argument("--title", default="")
    ap.add_argument("--publish", action="store_true", help="publish immediately (default: draft only)")
    ap.add_argument("--timeout", type=int, default=180)
    args = ap.parse_args()

    token = login()
    with tempfile.TemporaryDirectory() as tmp:
        media_path = convert(args.file, args.sound, tmp)
        print(f"media: {media_path} ({os.path.getsize(media_path)//1024} KB)")
        media_id = upload_media(token, media_path, alt=args.alt)
        print(f"uploaded: {media_id}")

    post = _req("POST", "/api/v1/content/posts", token=token, data={
        "content_text": args.caption,
        "media_ids": [media_id],
        "target_account_ids": args.account,
        "status": "draft",
    })
    pid = post["id"]
    print(f"post: {pid}")

    if not args.publish:
        print("draft created (no --publish)")
        return 0

    _req("POST", f"/api/v1/content/posts/{pid}/publish-now", token=token)
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        time.sleep(10)
        p = _req("GET", f"/api/v1/content/posts/{pid}", token=token)
        status = p.get("status")
        targets = [(t.get("platform"), t.get("status"), t.get("platform_url") or t.get("error_message"))
                   for t in p.get("targets", [])]
        print(f"  status={status} targets={targets}")
        if status in ("published", "failed"):
            return 0 if status == "published" else 1
    print("timed out waiting for publish", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
