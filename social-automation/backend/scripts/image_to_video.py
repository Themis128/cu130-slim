#!/usr/bin/env python3
"""Still image -> social-ready MP4 (Ken Burns + audio bed).

Turns one image into a short video post: gentle zoom over a dark
blurred backdrop, H.264/yuv420p, AAC, +faststart — the same encode
contract as gen_branded_video.py (LinkedIn/TikTok-safe).

    # cert post — 6s, generated chime bed
    python scripts/image_to_video.py cert.png --out cert.mp4 --audio chime

    # custom bed or existing audio file
    python scripts/image_to_video.py shot.png --out v.mp4 --audio /path/bed.wav
    python scripts/image_to_video.py shot.png --out v.mp4 --audio none

Aspect handling: the source is fit-inside the chosen --aspect canvas over
a blurred, darkened copy of itself (letterbox), so nothing is cropped.

Runs anywhere ffmpeg is on PATH — inside social-api:
    docker compose exec -T social-api python3 /app/scripts/image_to_video.py ...
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from audio_bed import render as render_bed  # noqa: E402

# LinkedIn-safe canvases: (width, height) ≤1920px/side.
ASPECTS = {
    "16:9": (1280, 720),
    "1:1": (1080, 1080),
    "4:5": (864, 1080),
    "9:16": (720, 1280),
}


def build_video(
    image: Path,
    out: Path,
    audio: str,
    seconds: float,
    aspect: str,
    fps: int,
    zoom: float,
) -> Path:
    width, height = ASPECTS[aspect]
    frames = int(seconds * fps)

    with tempfile.TemporaryDirectory() as td:
        # Audio: preset name -> synth bed via audio_bed; path -> use as-is;
        # none -> silent AAC track (platforms want an audio stream present).
        if audio in ("chime", "pad", "whoosh", "tick"):
            a_src = Path(td) / "bed.wav"
            render_bed(audio, seconds, a_src)
        elif audio != "none":
            a_src = Path(audio)
        else:
            a_src = Path(td) / "silence.wav"
            subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=duration={seconds:.2f}", "-ac", "2", "-ar", "44100", str(a_src)],
                check=True,
                capture_output=True,
            )

        # zoompan: d=frames expands one still into the full clip; z eases in.
        v_filter = (
            f"split[bg][fg];"
            f"[bg]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},boxblur=20:2,eq=brightness=-0.25[b];"
            f"[fg]scale={width}:{height}:force_original_aspect_ratio=decrease[f];"
            f"[b][f]overlay=(W-w)/2:(H-h)/2,zoompan=z='min({1 + zoom:.4f},zoom+{zoom / frames:.6f})'"
            f":d={frames}:s={width}x{height}:fps={fps},format=yuv420p[v]"
        )
        cmd = [
            "ffmpeg",
            "-y",
            "-loop",
            "1",
            "-framerate",
            str(fps),
            "-i",
            str(image),
            "-i",
            str(a_src),
            "-filter_complex",
            v_filter,
            "-map",
            "[v]",
            "-map",
            "1:a",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(fps),
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            "-t",
            f"{seconds:.2f}",
            "-shortest",
            "-movflags",
            "+faststart",
            str(out),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            sys.exit(f"ffmpeg failed:\n{proc.stderr[-2000:]}")
    print(f"{out}  ({aspect}, {seconds:.1f}s, audio={audio})")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--audio", default="chime", help="'chime'|'pad'|'whoosh'|'tick' (synth bed), a file path, or 'none'")
    ap.add_argument("--seconds", type=float, default=6.0)
    ap.add_argument("--aspect", choices=tuple(ASPECTS), default="16:9")
    ap.add_argument("--fps", type=int, default=25)
    ap.add_argument("--zoom", type=float, default=0.06, help="Total zoom delta across the clip")
    args = ap.parse_args()
    if not args.image.is_file():
        sys.exit(f"not found: {args.image}")
    build_video(args.image, args.out, args.audio, args.seconds, args.aspect, args.fps, args.zoom)


if __name__ == "__main__":
    main()
