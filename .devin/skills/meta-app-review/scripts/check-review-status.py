#!/usr/bin/env python3
"""Check Meta App Review submission status via Graph API.
Usage: check-review-status.py [app_id] [access_token]"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "meta-account-restriction/scripts"))
from _common import fb_token, graph, show  # noqa: E402

app_id = sys.argv[1] if len(sys.argv) > 1 else "1936126137016578"
token = fb_token(sys.argv[2] if len(sys.argv) > 2 else "")

print(f"Checking App Review status for app {app_id}...\n")
show(graph(f"/{app_id}/app_review_status", {"access_token": token}))

submission_id = "2047300442565813"
print(f"\nSubmission {submission_id}:")
show(graph(f"/{submission_id}", {
    "access_token": token, "fields": "status,submitted_time,permissions"}))
