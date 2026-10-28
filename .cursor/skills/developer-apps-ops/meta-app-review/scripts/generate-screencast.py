#!/usr/bin/env python3
"""Generate a screencast MP4 from a folder of PNG screenshots using ffmpeg.
Runs inside the social-api container (has ffmpeg installed).
Usage: generate-screencast.py [frames_dir] [output_path] [framerate]"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import repo_root  # noqa: E402

frames_dir = sys.argv[1] if len(sys.argv) > 1 else "/tmp/screencast-frames"
output_path = sys.argv[2] if len(sys.argv) > 2 else "/tmp/screencast-output/cloudless-screencast.mp4"
framerate = sys.argv[3] if len(sys.argv) > 3 else "5"

print(f"Generating screencast from {frames_dir} → {output_path}")
print(f"Framerate: {framerate} fps\n")

frames = sorted(Path(frames_dir).glob("frame_*.png"))
if not frames:
    print(f"Error: No frame_*.png files found in {frames_dir}")
    print("Expected files: frame_00.png, frame_01.png, ...")
    sys.exit(1)
print(f"Found {len(frames)} frames")

Path(output_path).parent.mkdir(parents=True, exist_ok=True)

subprocess.run([
    "docker", "compose", "exec", "-T", "social-api", "ffmpeg", "-y",
    "-framerate", framerate,
    "-i", "/tmp/screencast-frames/frame_%02d.png",
    "-c:v", "libx264", "-preset", "slow", "-crf", "20",
    "-pix_fmt", "yuv420p", "-s", "1280x720",
    "/tmp/screencast-output/cloudless-screencast.mp4"],
    cwd=repo_root(), check=True)

subprocess.run(["docker", "compose", "cp",
                "social-api:/tmp/screencast-output/cloudless-screencast.mp4",
                output_path], cwd=repo_root(), check=True)

size_mb = Path(output_path).stat().st_size / (1024 * 1024)
print(f"\nScreencast generated: {output_path}")
print(f"Size: {size_mb:.1f}M")
print(f"Duration: ~{len(frames) / int(framerate):.1f} seconds")
