#!/usr/bin/env python3
"""reauth_urls.py — print fresh OAuth authorize URLs for twitter + linkedin.

    reauth_urls.py          # prints both URLs
    reauth_urls.py twitter  # one platform only
"""
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2] / "offer-survey-ops/scripts"))
import offer_survey as s

TEAM = "10024d25-2ea4-40ea-86c6-ba89f515a2eb"

def main() -> int:
    plat = sys.argv[1] if len(sys.argv) > 1 else None
    t = s.login()
    for p in (plat and [plat] or ["twitter", "linkedin"]):
        try:
            r = s.req(f"/api/v1/auth/oauth/{p}/authorize?team_id={TEAM}", t, method="GET")
            print(f"{p}:\n{r['authorization_url']}\n")
        except Exception as e:
            print(f"{p}: {e}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
