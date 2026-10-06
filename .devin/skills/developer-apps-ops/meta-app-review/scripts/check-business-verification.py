#!/usr/bin/env python3
"""Check Meta business verification status.
Usage: check-business-verification.py [business_id] [access_token]"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "meta-account-restriction/scripts"))
from _common import fb_token, graph, show  # noqa: E402

biz_id = sys.argv[1] if len(sys.argv) > 1 else "1558125105019725"
token = fb_token(sys.argv[2] if len(sys.argv) > 2 else "")

print(f"Checking business verification for portfolio {biz_id}...\n")
show(graph(f"/{biz_id}", {
    "access_token": token,
    "fields": "name,verification_status,vertical,legal_name,business_address"}))

print("""
Verification URL: https://developers.facebook.com/apps/1936126137016578/app-review/verification

If verification is blocked, check:
  1. Personal account status: https://www.facebook.com/account_status
  2. Account Quality: https://www.facebook.com/accountquality
  3. Business Support Home: https://www.facebook.com/business-support-home/""")
