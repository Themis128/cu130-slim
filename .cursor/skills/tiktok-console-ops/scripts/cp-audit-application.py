#!/usr/bin/env python3
"""Fill/submit the Content Posting API (Direct Post) audit application.
Default: fills through the Review step without submitting (dry run).
  cp-audit-application.py           — prepare only, screenshots to out/
  cp-audit-application.py --submit  — tick declarations + submit"""

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import NODE_WORK, ROOT, ensure_playwright, env_key, run_mjs  # noqa: E402

ensure_playwright()
# script lives alongside this file (not in lib/)
src = Path(__file__).resolve().parent / "cp-audit-application.mjs"
shutil.copy(src, NODE_WORK / src.name)

demo = Path(env_key("TT_AUDIT_VIDEO_HOST") or
            str(ROOT / "docs/tiktok-demo/videos/tiktok-demo-v2.mp4"))
shutil.copy(demo, NODE_WORK / "tiktok-demo-v2.mp4")

args = sys.argv[1:] or ["--no-submit-flag"]
sys.exit(run_mjs("cp-audit-application.mjs", extra_args=args))
