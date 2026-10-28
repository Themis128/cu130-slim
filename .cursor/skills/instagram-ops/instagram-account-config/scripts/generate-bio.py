#!/usr/bin/env python3
"""Generate a stylish Instagram bio from LinkedIn profile data.
Usage: generate-bio.py --name "Cloudless" --title "Founder @ " --skills "..." ..."""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import repo_root  # noqa: E402

sys.exit(subprocess.run(
    ["docker", "compose", "exec", "-T", "social-api", "python",
     "/app/app/scripts/instagram_bio_generator.py", *sys.argv[1:]],
    cwd=repo_root()).returncode)
