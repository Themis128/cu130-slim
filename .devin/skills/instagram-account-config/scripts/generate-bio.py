#!/usr/bin/env python3
"""Generate a stylish Instagram bio from LinkedIn profile data"""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import api_base, api_login, env, request, usage  # noqa: E402

subprocess.run(['docker', 'compose', 'exec', '-T', 'social-api', 'python', '/app/app/scripts/instagram_bio_generator.py', f"$@"], check=True)
