#!/usr/bin/env python3
"""Print an appeal template for Meta account restriction submission.
Usage: print-appeal-template.py [ad_account_id] [business_id]"""

import sys

AD_ID = sys.argv[1] if len(sys.argv) > 1 else "657781691826702"
BIZ_ID = sys.argv[2] if len(sys.argv) > 2 else "1558125105019725"

print("""================================================================
META ACCOUNT RESTRICTION APPEAL TEMPLATE
================================================================

Subject: Request Review — Ad Account AD_ACCOUNT_ID

Dear Meta Review Team,

I'm requesting review of the restriction shown on Account Quality for
ad account AD_ACCOUNT_ID under Business Manager BIZ_ID.

Policy cited (as shown on Account Quality):
  Advertising Standards affecting business assets — non-compliance due to
  too many ads rejected, attempting to circumvent ad review process,
  fraudulent behavior, or association with untrustworthy accounts.

What happened:
  The ad account was disabled on Jan 24, 2021. I was not actively
  advertising at the time and was not aware of the specific violations
  that triggered the restriction. I have since reviewed Meta's
  Advertising Standards and corrected all issues on my end.

Corrective action taken:
  1. Reviewed and understood Meta's Advertising Standards
  2. Removed any non-compliant content or assets
  3. Verified business identity and legitimacy of cloudless.gr
  4. Committed to full compliance with all Meta policies going forward

Why I need this resolved:
  I am the admin of a Meta Developer app (Cloudless, App ID:
  1936126137016578) and need to complete business verification for the
  cloudless.gr business portfolio. The personal account restriction
  blocks business verification, which blocks my App Review submission.
  All other App Review requirements are complete.

Supporting documentation:
  - Business registration documents for cloudless.gr
  - Screenshots of the App Review submission showing all requirements
    completed except verification
  - Screenshots of the restriction dialog

I am committed to compliance with Meta's policies and request
reinstatement of this account so I can complete business verification.

Thank you,
Themistoklis Baltzakis — Owner, cloudless.gr

================================================================
NOTES:""".replace("AD_ACCOUNT_ID", AD_ID).replace("BIZ_ID", BIZ_ID))

print(f"""- Replace AD_ACCOUNT_ID with: {AD_ID}
- Replace BIZ_ID with: {BIZ_ID}
- Submit through: https://www.facebook.com/accountquality
  → Select restricted account → Request Review
- If "Request Review" is not available, the appeal window has expired.
  Use: Add a second admin, or Report a Problem (meta-support-report skill).
================================================================""")
