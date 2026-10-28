#!/usr/bin/env python3
"""gen_branded_video.py — Cloudless branded slideshow video, zero AI inference.

Renders slides with the pipeline's PIL brand composer
(`compose_branded_slide`, bg_img=None) inside the social-api container,
assembles an MP4 with ffmpeg inside social-worker-media (host has no
ffmpeg/ffprobe), then runs LinkedIn-spec QA and optionally uploads a
draft post.

Stages (each skippable output is cached in --workdir):
  1. render  — slides spec JSON → 1080x1080 PNGs (PIL, seconds, no GPU)
  2. assemble — PNGs → MP4 (zoompan + xfade, H.264 yuv420p, silent AAC,
                +faststart)
  3. qa      — ffprobe spec checks + frame extraction for eyeball review
  4. upload  — --upload: media asset + DRAFT post (--publish also fires
               publish-now; default stays draft for review)

Usage:
  gen_branded_video.py --spec slides.json --out /tmp/out.mp4
  gen_branded_video.py --spec slides.json --out /tmp/out.mp4 \
      --upload --account 9c4451bb-e820-489f-8676-76ddbc788ffe \
      --caption-file caption.txt --hashtags a,b,c --link https://cloudless.gr/contact
  # add --publish to publish the draft immediately (use with care — the
  # stored caption should be verified first, auto-correct can mangle it)

slides.json shape:
  {"slides": [
    {"slide_type": "cover|content|stat|cta",      # required-ish, defaults content
     "motif": "cover|servers|pricing|calendar|rocket|chat|stat|cta|compare",
     "title": "...", "body": "...", "highlight": "...",
     "chart_data": {...} | null}, ...]}

WARNING — motif stat defaults: `servers`, `pricing` and `rocket` render
BUILT-IN metric cards ("85% reduction", "99.9% uptime", "80% ops saved")
when chart_data is empty. Those are NOT verified Cloudless numbers —
always pass chart_data overrides or pick chat/stat/calendar/compare.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

SKILL_LIB = Path(__file__).resolve().parents[2] / "_lib"
sys.path.insert(0, str(SKILL_LIB))
from skill_http import request, social_api, upload  # noqa: E402

RENDER_CONTAINER = "social-api"
FFMPEG_CONTAINER = "social-worker-media"
C_DIR = "/tmp/_bv_slides"

# LinkedIn video spec (official docs): MP4 75KB–500MB, 3s–30min,
# H.264/VP8 ≤30fps, 360–1920px/side, ratios 16:9/1:1/4:5/9:16.
QA_LIMITS = {"min_bytes": 75_000, "max_bytes": 500 * 1024 * 1024,
             "min_dur": 3.0, "max_dur": 1800.0, "max_fps": 30.0}


def sh(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def die(msg: str, code: int = 1) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def render_slides(spec: dict, workdir: Path) -> list[Path]:
    """Run compose_branded_slide inside social-api; copy PNGs to workdir."""
    slides = spec["slides"]
    runner = (
        "import json, sys\n"
        "sys.path.insert(0, '/app')\n"
        "from app.services.carousel_pipeline import compose_branded_slide\n"
        f"slides = json.loads({json.dumps(json.dumps(slides))})\n"
        "for i, s in enumerate(slides):\n"
        "    img = compose_branded_slide(None, index=i, total=len(slides),\n"
        "        slide_type=s.get('slide_type','content'), title=s.get('title',''),\n"
        "        body=s.get('body',''), highlight=s.get('highlight'),\n"
        "        motif=s.get('motif'), chart_data=s.get('chart_data'))\n"
        f"    img.save({C_DIR!r} + f'/slide_{{i:02d}}.png')\n"
        "    print('slide', i, img.size)\n"
    )
    # spec → container stdin runner; PNGs land in C_DIR inside the container
    prep = sh(["docker", "exec", RENDER_CONTAINER, "mkdir", "-p", C_DIR])
    if prep.returncode != 0:
        die(f"cannot mkdir in {RENDER_CONTAINER}: {prep.stderr[:300]}")
    r = subprocess.run(["docker", "exec", "-i", RENDER_CONTAINER, "python", "-"],
                       input=runner, capture_output=True, text=True)
    print(r.stdout, end="")
    if r.returncode != 0:
        die(f"slide render failed: {r.stderr[-800:]}")
    dest = workdir / "slides"
    dest.mkdir(parents=True, exist_ok=True)
    cp = sh(["docker", "cp", f"{RENDER_CONTAINER}:{C_DIR}/.", str(dest)])
    if cp.returncode != 0:
        die(f"docker cp slides failed: {cp.stderr[:300]}")
    pngs = sorted(dest.glob("slide_*.png"))
    if not pngs:
        die("no slides rendered")
    print(f"rendered {len(pngs)} slides → {dest}")
    return pngs


def assemble(pngs: list[Path], out: Path, workdir: Path,
             slide_s: float, fade: float, fps: int, size: int) -> Path:
    """ffmpeg slideshow inside social-worker-media: zoompan + xfade chain."""
    n = len(pngs)
    per = slide_s + fade  # each stream runs solo + fade tail
    c_in = f"{C_DIR}/ffin"
    sh(["docker", "exec", FFMPEG_CONTAINER, "mkdir", "-p", c_in])
    for p in pngs:
        r = sh(["docker", "cp", str(p), f"{FFMPEG_CONTAINER}:{c_in}/{p.name}"])
        if r.returncode != 0:
            die(f"docker cp {p.name} → {FFMPEG_CONTAINER} failed: {r.stderr[:300]}")

    # Ken Burns: single-frame input + zoompan d=<frames> expands each still
    # to `per` seconds; xfade overlaps the `fade` tail into the next slide.
    frames = int(per * fps)
    inputs: list[str] = []
    for p in pngs:
        inputs += ["-i", f"{c_in}/{p.name}"]
    fc = [
        f"[{i}:v]scale={size * 2}:{size * 2},zoompan="
        f"z='min(1+0.0012*on,1.14)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d={frames}:s={size}x{size}:fps={fps},format=yuv420p[v{i}]"
        for i in range(n)
    ]
    prev = "v0"
    for i in range(1, n):
        out_tag = "vout" if i == n - 1 else f"x{i}"
        fc.append(f"[{prev}][v{i}]xfade=transition=fade:duration={fade}:offset={i * slide_s:.2f}[{out_tag}]")
        prev = out_tag
    total = slide_s * n + fade

    c_mp4 = f"{c_in}/out.mp4"
    cmd = ["docker", "exec", FFMPEG_CONTAINER, "ffmpeg", "-y"] + inputs + [
        "-f", "lavfi", "-t", f"{total:.2f}", "-i", "anullsrc=r=44100:cl=stereo",
        "-filter_complex", ";".join(fc),
        "-map", "[vout]", "-map", f"{n}:a",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(fps),
        "-c:a", "aac", "-b:a", "96k", "-shortest",
        "-movflags", "+faststart", "-t", f"{total:.2f}", c_mp4]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        die(f"ffmpeg failed:\n{r.stderr[-1500:]}")
    cp = sh(["docker", "cp", f"{FFMPEG_CONTAINER}:{c_mp4}", str(out)])
    if cp.returncode != 0 or not out.exists():
        die(f"docker cp mp4 failed: {cp.stderr[:300]}")
    print(f"video: {out} ({out.stat().st_size / 1e6:.2f} MB, {total:.1f}s)")
    return out


def qa(mp4: Path, workdir: Path, n_slides: int, slide_s: float, fade: float) -> dict:
    """ffprobe spec check inside the media container + QA frames on host."""
    c_mp4 = f"{C_DIR}/ffin/out.mp4"
    r = sh(["docker", "exec", FFMPEG_CONTAINER, "ffprobe", "-v", "quiet",
            "-print_format", "json", "-show_format", "-show_streams", c_mp4])
    if r.returncode != 0:
        die(f"ffprobe failed: {r.stderr[:300]}")
    probe = json.loads(r.stdout)
    vs = next(s for s in probe["streams"] if s["codec_type"] == "video")
    fmt = probe["format"]
    num, den = (int(x) for x in vs["avg_frame_rate"].split("/"))
    info = {
        "duration": float(fmt["duration"]), "bytes": int(fmt["size"]),
        "codec": vs["codec_name"], "w": vs["width"], "h": vs["height"],
        "fps": num / den, "pix_fmt": vs.get("pix_fmt"),
    }
    problems = []
    if info["codec"] not in ("h264", "vp8"):
        problems.append(f"codec {info['codec']}")
    if info["pix_fmt"] != "yuv420p":
        problems.append(f"pix_fmt {info['pix_fmt']}")
    if not (QA_LIMITS["min_dur"] <= info["duration"] <= QA_LIMITS["max_dur"]):
        problems.append(f"duration {info['duration']:.1f}s")
    if not (QA_LIMITS["min_bytes"] <= info["bytes"] <= QA_LIMITS["max_bytes"]):
        problems.append(f"size {info['bytes']}")
    if info["fps"] > QA_LIMITS["max_fps"]:
        problems.append(f"fps {info['fps']:.1f}")
    if not (360 <= info["w"] <= 1920 and 360 <= info["h"] <= 1920):
        problems.append(f"dims {info['w']}x{info['h']}")
    print("PROBE:", json.dumps(info, indent=1))
    if problems:
        die("QA spec failures: " + ", ".join(problems))

    # QA frames — center of each slide's solo window (legibility review)
    fdir = workdir / "qa_frames"
    fdir.mkdir(exist_ok=True)
    for i in range(n_slides):
        t = i * slide_s + fade + (slide_s - fade) / 2 if i else slide_s / 2
        sh(["docker", "exec", FFMPEG_CONTAINER, "ffmpeg", "-y", "-ss", f"{t:.1f}",
            "-i", c_mp4, "-frames:v", "1", f"{c_mp4}.f{i}.png"])
        sh(["docker", "cp", f"{FFMPEG_CONTAINER}:{c_mp4}.f{i}.png",
            str(fdir / f"slide{i:02d}_t{t:04.1f}s.png")])
    print(f"qa frames → {fdir}  (inspect every frame before publishing)")
    return info


def upload_draft(mp4: Path, args) -> dict:
    api, token = social_api()
    teams = request("GET", f"{api}/api/v1/teams", token=token)
    team_id = teams[0]["id"]
    asset = upload("POST", f"{api}/api/v1/media/upload?team_id={team_id}",
                   str(mp4), extra={"alt_text": args.alt or "branded video",
                                    "tags": args.tags or "video,brand"},
                   token=token, timeout=180)
    mid = asset.get("id")
    print("media asset:", mid)
    caption = Path(args.caption_file).read_text() if args.caption_file else ""
    post = request("POST", f"{api}/api/v1/content/posts?team_id={team_id}",
                   token=token, data={
                       "content_text": caption,
                       "media_ids": [mid],
                       "hashtags": [h.strip() for h in (args.hashtags or "").split(",") if h.strip()],
                       "link_url": args.link,
                       "target_account_ids": [args.account],
                   })
    pid = post.get("id")
    print(f"draft post: {pid} (status {post.get('status')})")
    print("VERIFY the stored caption before publishing — auto-correct can "
          "mangle € / proper nouns / hashtags.")
    if args.publish:
        pub = request("POST",
                      f"{api}/api/v1/content/posts/{pid}/publish-now?team_id={team_id}",
                      token=token)
        print("publish-now:", pub.get("status"))
    return post


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spec", required=True, help="slides JSON file")
    ap.add_argument("--out", required=True, help="output .mp4 path")
    ap.add_argument("--workdir", help="scratch dir (default: <out>.work)")
    ap.add_argument("--slide-seconds", type=float, default=5.0)
    ap.add_argument("--fade", type=float, default=0.8)
    ap.add_argument("--fps", type=int, default=25)
    ap.add_argument("--size", type=int, default=1080, help="square px (1:1)")
    ap.add_argument("--skip-render", action="store_true",
                    help="reuse PNGs already in <workdir>/slides")
    ap.add_argument("--upload", action="store_true", help="upload + create DRAFT post")
    ap.add_argument("--account", help="target account UUID (required with --upload)")
    ap.add_argument("--caption-file", help="post caption text file")
    ap.add_argument("--hashtags", help="comma-separated hashtags")
    ap.add_argument("--link", help="post link_url (CTA)")
    ap.add_argument("--alt", help="media alt_text")
    ap.add_argument("--tags", help="media tags")
    ap.add_argument("--publish", action="store_true",
                    help="publish-now after drafting (verify caption first!)")
    args = ap.parse_args()

    spec = json.loads(Path(args.spec).read_text())
    slides = spec.get("slides") or []
    if len(slides) < 2:
        die("spec needs >= 2 slides")
    # guard: stat-card motifs without chart_data render invented metrics
    risky = {"servers", "pricing", "rocket"}
    for i, s in enumerate(slides):
        if s.get("motif") in risky and not s.get("chart_data"):
            print(f"WARN slide {i}: motif {s['motif']!r} renders built-in "
                  f"metric cards — real numbers only, pass chart_data",
                  file=sys.stderr)

    out = Path(args.out).resolve()
    workdir = Path(args.workdir or f"{out}.work").resolve()
    workdir.mkdir(parents=True, exist_ok=True)

    pngs = sorted((workdir / "slides").glob("slide_*.png")) if args.skip_render else []
    if not pngs:
        pngs = render_slides(spec, workdir)
    mp4 = assemble(pngs, out, workdir, args.slide_seconds, args.fade, args.fps, args.size)
    qa(mp4, workdir, len(slides), args.slide_seconds, args.fade)
    if args.upload:
        if not args.account:
            die("--upload requires --account <uuid>")
        if not args.caption_file:
            die("--upload requires --caption-file (posts never ship without copy)")
        upload_draft(mp4, args)
    print("DONE:", mp4)


if __name__ == "__main__":
    main()
