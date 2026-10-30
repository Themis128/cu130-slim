#!/usr/bin/env python3
"""gate_check.py — open issues → one-line unblock each."""
import json
import subprocess
import sys

UNBLOCK = {
    786: "accept Cloud API terms on business.facebook.com/wa/manage/home/ (or debug-Chrome cdp_click)",
    788: "rotate INSTAGRAM_PASSWORD in .env → rerun session_bootstrap.py",
    789: "edit website in the TikTok app (tiktok_profile_edit.py via ADB)",
    785: "upload evidence in Meta console → leads_retrieval App Review",
    793: "reply 'approve' or recipient ids on the issue",
    794: "reply 'approve' — but li_at re-auth must happen first",
    795: "reply 'approve' — positioning/pricing draft on the issue",
    791: "reply with verdict on the flagged inbox shares",
}
issues = json.loads(subprocess.run(
    ["gh", "issue", "list", "--repo", "Themis128/cu130-slim",
     "--state", "open", "--json", "number,title", "--limit", "30"],
    capture_output=True, text=True).stdout or "[]")
if not issues:
    print("no open issues"); sys.exit(0)
for i in issues:
    hint = UNBLOCK.get(i["number"], "see issue — reply consume loop watches it")
    print(f"#{i['number']:<4} {i['title'][:58]}\n       → {hint}")
