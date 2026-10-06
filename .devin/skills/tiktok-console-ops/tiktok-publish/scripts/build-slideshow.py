#!/usr/bin/env python3
"""Build a slideshow video from image media assets in the SocialAuto library.
Downloads each asset via the media/view endpoint, builds an MP4 with ffmpeg
in the comfyui container, and outputs the path to the resulting video.

Usage: build-slideshow.py <asset-id-1> [<asset-id-2> ...]
       [--seconds 3] [--output /tmp/video.mp4]"""

import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import repo_root, request, social_api, usage  # noqa: E402

seconds = "3"
output = "/tmp/tiktok-slideshow.mp4"
asset_ids: list[str] = []

i = 1
while i < len(sys.argv):
    arg = sys.argv[i]
    if arg in ("--seconds", "--output"):
        if i + 1 >= len(sys.argv):
            usage("build-slideshow.py <asset-id>... [--seconds 3] "
                  "[--output /tmp/video.mp4]")
        if arg == "--seconds":
            seconds = sys.argv[i + 1]
        else:
            output = sys.argv[i + 1]
        i += 2
    elif arg.startswith("-"):
        print(f"Unknown arg: {arg}", file=sys.stderr)
        sys.exit(2)
    else:
        asset_ids.append(arg)
        i += 1

if not asset_ids:
    usage("build-slideshow.py <asset-id-1> [<asset-id-2> ...] "
          "[--seconds 3] [--output /tmp/video.mp4]")

ROOT = repo_root()
api, token = social_api()

input_dir = ROOT / "storage-user/input/tiktok-slides"
input_dir.mkdir(parents=True, exist_ok=True)
for old in input_dir.glob("slide-*.png"):
    old.unlink()

print(f"Downloading {len(asset_ids)} assets...")
n = 0
for aid in asset_ids:
    n += 1
    try:
        asset = request("GET", f"{api}/api/v1/media/assets/{aid}", token=token)
        storage_path = asset.get("storage_path", "")
    except SystemExit:
        storage_path = ""
    if not storage_path:
        print(f"  Asset {aid}: not found, skipping")
        continue

    dest = input_dir / f"slide-{n}.png"
    url = f"https://social.cloudless.gr/api/v1/media/view?path={storage_path}"
    try:
        req = urllib.request.Request(url)
        data = urllib.request.urlopen(req, timeout=60).read()
        dest.write_bytes(data)
    except Exception:
        data = b""
    if len(data) < 100:
        print(f"  Asset {aid}: fetching from MinIO...")
        fetch_py = (
            "import asyncio\n"
            "from app.services import minio_storage\n"
            "async def main():\n"
            f"    data = await minio_storage.get_object({storage_path!r})\n"
            f"    open('/tmp/slide-{n}.png', 'wb').write(data)\n"
            "    print(f'  Downloaded {len(data)} bytes')\n"
            "asyncio.run(main())\n")
        subprocess.run(
            ["docker", "compose", "exec", "-T", "social-api",
             "python3", "-c", fetch_py], cwd=ROOT)
        subprocess.run(
            ["docker", "compose", "cp", f"social-api:/tmp/slide-{n}.png",
             str(dest)], cwd=ROOT)
    print(f"  slide-{n}.png ← {aid} ({storage_path})")

num_slides = len(list(input_dir.glob("slide-*.png")))
if num_slides < 1:
    print("No slides downloaded. Aborting.")
    sys.exit(1)

print(f"Building slideshow: {num_slides} slides, {seconds}s each, 1080x1080...")

inner = f"""
cd /home/user/ComfyUI/input/tiktok-slides
LIST=/tmp/slideshow.txt
> $LIST
for n in $(seq 1 {num_slides}); do
  echo "file '/home/user/ComfyUI/input/tiktok-slides/slide-${{n}}.png'" >> $LIST
  echo "duration {seconds}" >> $LIST
done
echo "file '/home/user/ComfyUI/input/tiktok-slides/slide-{num_slides}.png'" >> $LIST

ffmpeg -y -f concat -safe 0 -i $LIST \\
  -f lavfi -i anullsrc=channel_layout=stereo:sample_rate=44100 \\
  -vf 'scale=1080:1080:force_original_aspect_ratio=decrease,pad=1080:1080:(ow-iw)/2:(oh-ih)/2:black,format=yuv420p,fps=30' \\
  -c:v libx264 -preset medium -crf 20 -pix_fmt yuv420p \\
  -c:a aac -b:a 128k -shortest \\
  /home/user/ComfyUI/output/tiktok-slideshow.mp4 2>&1 | tail -8
"""
r = subprocess.run(
    ["docker", "exec", "social-media-comfyui-gpu", "bash", "-c", inner],
    cwd=ROOT)
if r.returncode != 0:
    sys.exit(r.returncode)

built = ROOT / "storage-user/output/tiktok-slideshow.mp4"
shutil.copy(built, output)
size = Path(output).stat().st_size
r = subprocess.run(
    ["docker", "exec", "social-media-comfyui-gpu", "ffprobe", "-v", "quiet",
     "-show_entries", "format=duration", "-of", "csv=p=0",
     "/home/user/ComfyUI/output/tiktok-slideshow.mp4"],
    capture_output=True, text=True)
duration = r.stdout.strip().splitlines()[0] if r.stdout.strip() else "?"

print(f"""
Slideshow built: {output}
  Slides: {num_slides}
  Duration: {duration}s
  Size: {size // 1024}KB

Upload to media library:
  python3 .devin/skills/socialauto-media/scripts/upload-media.py {output}
""")
