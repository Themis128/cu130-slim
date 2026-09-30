#!/usr/bin/env python3
"""Build SocialAuto daily digest inside social-api and print markdown for
Slack MCP. Does not post — agent posts via slack_send_message to C0BT263L17U.
Usage: post-digest-via-mcp.py [output-file]"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import repo_root  # noqa: E402

ROOT = repo_root()
out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / ".tmp-socialauto-digest-slack.md"
script = ROOT / ".devin/skills/social-stack-ops/scripts/build_digest_for_slack.py"

subprocess.run(["docker", "cp", str(script),
                "social-api:/tmp/build_digest_for_slack.py"], check=True)
subprocess.run(["docker", "exec", "social-api", "python",
                "/tmp/build_digest_for_slack.py"], check=True)
subprocess.run(["docker", "cp", "social-api:/tmp/socialauto-digest-slack.md",
                str(out)], check=True)
print(f"Wrote {out} — post contents with Slack MCP slack_send_message channel_id=C0BT263L17U")
