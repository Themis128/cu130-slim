#!/usr/bin/env python3
"""Full Instagram account setup: login, generate bio, update profile, verify.
Usage: full-setup.py --username t_baltzakis --name "Cloudless" --title "Founder @ "
  --skills "Cloud Architect · Azure · AWS" --experience "15+ yrs building systems"
  --location "Athens" --links "cloudless.gr | baltzakisthemis.com" --style bold-brand"""

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import repo_root  # noqa: E402

SCRIPT_DIR = Path(__file__).resolve().parent
args = {"--title": "Founder @ ", "--location": "Athens", "--style": "bold-brand"}
i = 1
while i < len(sys.argv):
    if sys.argv[i].startswith("--") and i + 1 < len(sys.argv):
        args[sys.argv[i]] = sys.argv[i + 1]
        i += 2
    else:
        i += 1

print("========================================")
print("  Instagram Full Account Setup")
print("========================================")
print(f'  Username: {args.get("--username", "")}')
print(f'  Name: {args.get("--name", "")}')
print(f'  Style: {args.get("--style", "")}')
print("========================================\n")


def docker_py(script: str, *a: str) -> str:
    r = subprocess.run(
        ["docker", "compose", "exec", "-T", "social-api", "python",
         f"/app/app/scripts/{script}", *a],
        cwd=repo_root(), capture_output=True, text=True)
    return r.stdout


print("Step 1: Checking browser session...")
subprocess.run([sys.executable, str(SCRIPT_DIR / "check-session.py")])

print("\nStep 2: Generating stylish bio...")
bio_args = []
for k in ("--name", "--title", "--skills", "--experience", "--location",
          "--links", "--style"):
    bio_args += [k, args.get(k, "")]
bio = docker_py("instagram_bio_generator.py", *bio_args).strip()
print(f"Generated bio:\n{bio}\n")

print("Step 3: Updating Instagram profile...")
print(docker_py("instagram_profile_update.py", "--bio", bio))

print("\nStep 4: Verifying...")
time.sleep(5)
print(docker_py("instagram_profile_update.py", "--verify-bio",
                args.get("--name", "")))

print("\nStep 5: Settings checklist...")
print(docker_py("instagram_settings_checklist.py", "--list"))

print("\n========================================")
print("  Setup complete!")
print("========================================")
print(f'  Bio updated with style: {args.get("--style", "")}')
print("  Review the settings checklist above")
print("  and apply CRITICAL/HIGH items via VNC.")
print("========================================")
