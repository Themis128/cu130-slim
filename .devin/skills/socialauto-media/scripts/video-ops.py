#!/usr/bin/env python3
"""Video ops on SocialAuto media assets — trim/convert/attach-audio/mute/extract.

Usage:
  video-ops.py trim <asset_id> --start 0 --end 15 -o out.mp4
  video-ops.py convert <asset_id> --format webm -o out.webm
  video-ops.py attach-audio <video_id> <audio_id> [--no-loop] -o out.mp4
  video-ops.py mute <asset_id> -o silent.mp4
  video-ops.py extract-audio <asset_id> -o track.mp3

Env: SOCIAL_API (default http://localhost:8083), SOCIAL_TOKEN.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import env, request  # noqa

API = env("SOCIAL_API") or "http://localhost:8083"
TOKEN = env("SOCIAL_TOKEN") or env("SOCIAL_ADMIN_TOKEN")

def die(msg, code=1):
    print(f"ERROR: {msg}", file=sys.stderr); sys.exit(code)

def call(op, asset_id, payload):
    url = f"{API}/api/v1/media/enhance/assets/{asset_id}/video/{op}"
    status, body = request("POST", url, token=TOKEN, json=payload, raw=True)
    if status != 200: die(f"{op} returned {status}: {body[:300] if isinstance(body,str) else body}")
    return body if isinstance(body, bytes) else body.encode()

def main():
    args = sys.argv[1:]
    if len(args) < 2:
        print(__doc__); sys.exit(2)
    op = args[0]; asset_id = args[1]
    kv = dict(zip(args[2::2], args[3::2]))
    out = kv.get("-o") or kv.get("--out")
    if not out: die("need -o OUTPUT")

    if op == "trim":
        b = call("trim", asset_id, {"start": float(kv.get("--start", 0)), "end": float(kv["--end"])})
    elif op == "convert":
        b = call("convert", asset_id, {"format": kv.get("--format", "mp4")})
    elif op == "attach-audio":
        b = call("attach-audio", asset_id, {"audio_asset_id": args[2], "loop_audio": "--no-loop" not in args})
        if "-o" in args: out = args[args.index("-o")+1]
    elif op == "mute":
        b = call("mute", asset_id, {})
    elif op == "extract-audio":
        b = call("audio", asset_id, {})
    else:
        die(f"unknown op {op}")
    Path(out).write_bytes(b)
    print(f"{op} → {out} ({len(b)} bytes)")

main()
