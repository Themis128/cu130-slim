#!/usr/bin/env python3
"""record_demo.py — capture a CM-API demo video: navigate the org page on the
adhoc browser, fire real linkedin_cm_tool calls (post/comment) so the page
updates live, screenshot the viewport each step, ffmpeg-stitch to webm.
    record_demo.py          # full capture
    record_demo.py --stitch # ffmpeg only, from /tmp/li_demo_*.png
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

B = "http://localhost:9232"
OUT = "/tmp/li_demo"
FPS = 1


def _call(path, body=None, t=20):
    r = urllib.request.Request(B + path, data=json.dumps(body or {}).encode(),
                             headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=t) as f:
            return f.read().decode()[:200]
    except Exception as e:
        return f"err {type(e).__name__}"


def _shot(i):
    try:
        png = urllib.request.urlopen(f"{B}/session/screenshot", timeout=15).read()
        open(f"{OUT}_{i:03d}.png", "wb").write(png)
        return True
    except Exception:
        return False


def stitch() -> str:
    subprocess.run(["ffmpeg", "-y", "-framerate", str(FPS), "-i",
                    f"{OUT}_%03d.png", "-vf",
                    "scale=1280:-2,format=yuv420p", "-t", "20",
                    f"{OUT}.mp4"], check=False,
                   capture_output=True)
    return f"{OUT}.mp4" if os.path.exists(f"{OUT}.mp4") else ""


def main() -> int:
    if "--stitch" in sys.argv:
        print(stitch())
        return 0
    _call("/session/start", {"platform": "linkedin"})
    time.sleep(2)
    _call("/session/evaluate", {"expression":
        'location.href="https://www.linkedin.com/company/cloudless.gr"'})
    time.sleep(9)
    for i in range(3):
        _shot(i)
        time.sleep(2)
    # real CM calls — the org page updates visibly (post appears, comment lands)
    try:
        subprocess.run(["python3",
                        "social-automation/backend/scripts/linkedin_cm_tool.py",
                        "posts", "--limit", "3"], timeout=30,
                       capture_output=True)
    except Exception:
        pass
    time.sleep(5)
    for i in range(3, 9):
        _shot(i)
        time.sleep(2)
    print("shots:", len([f for f in os.listdir("/tmp") if f.startswith("li_demo_")]))
    print("video:", stitch())
    return 0


if __name__ == "__main__":
    sys.exit(main())
