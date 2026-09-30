#!/usr/bin/env python3
"""Check Meta ad account restriction status via Graph API.
Usage: check-ad-account.py [ad_account_id] [access_token]"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import fb_token, graph, show  # noqa: E402

ad_id = sys.argv[1] if len(sys.argv) > 1 else "657781691826702"
token = fb_token(sys.argv[2] if len(sys.argv) > 2 else "")

print(f"Checking ad account {ad_id}...\n")
show(graph(f"/act_{ad_id}", {
    "access_token": token,
    "fields": "account_status,name,amount_spent,balance,currency,"
              "disable_reason,ad_account_detailed_status"}))

print(f"""
=== Status reference ===
1=ACTIVE, 2=DISABLED, 3=UNSETTLED, 7=PENDING_RISK_REVIEW
8=PENDING_SETTLEMENT, 9=IN_GRACE_PERIOD, 100=PENDING_REVIEW

=== Web-based check ===
Ad account detail: https://www.facebook.com/business-support-home/1134463867/{ad_id}/""")
