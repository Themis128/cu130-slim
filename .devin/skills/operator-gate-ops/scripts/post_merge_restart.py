#!/usr/bin/env python3
"""post_merge_restart.py — watch SocialAuto PRs until merged, then restart the
compose stack (social-api + workers + celery-beat) and health-check.

Usage:
  python post_merge_restart.py [pr ...]            # default: open wiring PRs
  python post_merge_restart.py --once              # check + restart if merged
  python post_merge_restart.py --timeout 7200      # give up after N seconds
"""
import json
import subprocess
import sys
import time

REPO = r"C:\Users\tbaltzakis\cu130-slim"
DEFAULT_PRS = ["1302", "1308", "1309", "1301"]
SERVICES = ["social-api", "social-worker-publishing", "social-worker-media",
            "social-worker-default", "social-worker-messenger", "celery-beat"]


def sh(args, cwd=REPO, timeout=120):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                          timeout=timeout, check=False)


def merged(pr):
    r = sh(["gh", "pr", "view", pr, "--json", "state", "--jq", ".state"])
    return r.stdout.strip() == "MERGED"


def restart():
    r = sh(["docker", "compose", "restart", *SERVICES], timeout=300)
    print("restart:", "ok" if r.returncode == 0 else r.stderr.strip()[:120])
    time.sleep(8)
    h = sh(["curl", "-s", "--max-time", "10", "http://localhost:8083/health"])
    print("api health:", h.stdout.strip()[:80] or "FAIL")
    ping = sh(["docker", "compose", "exec", "-T", "social-worker-publishing",
               "celery", "-A", "app.worker.celery_app", "inspect", "ping"], timeout=60)
    nodes = ping.stdout.count("pong")
    print(f"worker nodes: {nodes}/4")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    prs = args or DEFAULT_PRS
    once = "--once" in sys.argv
    timeout = int(next((a.split("=")[1] for a in sys.argv if a.startswith("--timeout=")), "7200"))
    deadline = time.time() + timeout

    pending = set(prs)
    while pending and time.time() < deadline:
        for pr in list(pending):
            if merged(pr):
                print(f"PR #{pr} MERGED — restarting services")
                restart()
                pending.remove(pr)
        if once or not pending:
            break
        print("still open:", ",".join(sorted(pending)), "— polling in 90s")
        time.sleep(90)
    if pending:
        print("timeout — still open:", ",".join(sorted(pending)))
        return 1
    print("all watched PRs merged + stack restarted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
