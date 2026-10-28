#!/usr/bin/env python3
"""Evaluate JavaScript in the browser and print the result.
Usage: bridge-eval.py <expression>"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import api_base, request, usage  # noqa: E402

bridge = api_base("BROWSER_BRIDGE_URL", "http://localhost:9223")
expr = sys.argv[1] if len(sys.argv) > 1 else usage("bridge-eval.py <expression>")
d = request("POST", f"{bridge}/session/evaluate", data={"expression": expr})
print(d.get("result", ""))
