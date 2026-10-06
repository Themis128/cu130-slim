#!/usr/bin/env python3
"""Check Meta business portfolio health and verification status.
Usage: check-business-portfolio.py [business_id] [access_token]"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import fb_token, graph, show  # noqa: E402

biz_id = sys.argv[1] if len(sys.argv) > 1 else "1558125105019725"
token = fb_token(sys.argv[2] if len(sys.argv) > 2 else "")

print(f"Checking business portfolio {biz_id}...\n")
show(graph(f"/{biz_id}", {
    "access_token": token,
    "fields": "name,verification_status,vertical,legal_name,"
              "business_address,primary_page,two_factor_required"}))

print(f"""
=== Web-based checks ===
Business portfolio detail: https://www.facebook.com/business-support-home/{biz_id}/
Business verification: https://developers.facebook.com/apps/1936126137016578/app-review/verification

If verification is blocked, the personal account admin has restrictions.
See: meta-account-restriction skill for resolution steps.""")
