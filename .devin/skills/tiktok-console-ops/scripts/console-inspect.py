#!/usr/bin/env python3
"""Login to TikTok developer console and dump Cloudless app state
(JSON + screenshots)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import ensure_playwright, run_mjs  # noqa: E402

ensure_playwright()
sys.exit(run_mjs("console-inspect.mjs"))
