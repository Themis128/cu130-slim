#!/usr/bin/env python3
"""Spellcheck text via LanguageTool.
Usage: spellcheck.py "text to check" """

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import request, social_api, usage  # noqa: E402

text = sys.argv[1] if len(sys.argv) > 1 else usage('spellcheck.py "text"')
api, token = social_api()
d = request("POST", f"{api}/api/v1/ai/spellcheck", token=token, data={"text": text})
print(json.dumps(d, indent=2, ensure_ascii=False))
