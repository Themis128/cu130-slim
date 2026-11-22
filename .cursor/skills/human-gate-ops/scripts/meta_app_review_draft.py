#!/usr/bin/env python3
"""meta_app_review_draft.py — print paste-ready text for the Meta App
Review submission (app 1936126137016578 "Cloudless", submission
2047300442565813).

Covers the leads_retrieval permission (Facebook Login for Business use
case). All wizard fields except Verification were saved previously —
see developer-apps-ops/SKILL.md "Submission state". This prints the
field-by-field copy to paste into the App Review form plus a
screencast checklist. Console submission itself has no API — this is
the text-prep half of the gate.

Usage: python3 meta_app_review_draft.py [--json]
"""
import json
import sys

APP_ID = "1936126137016578"
APP_NAME = "Cloudless"
SUBMISSION_ID = "2047300442565813"
BUSINESS_ID = "1558125105019725"
PRIVACY_URL = "https://social.cloudless.gr/privacy"
DATA_DELETION_URL = "https://social.cloudless.gr/data-deletion"
WEBHOOK_URL = "https://social.cloudless.gr/api/v1/messenger/webhook"

FIELDS = {
    "permission": "leads_retrieval",
    "use_case": (
        "Cloudless runs a Facebook lead-generation funnel for its SocialAuto "
        "platform. When a person submits a lead ad form on the Cloudless "
        "Facebook Page, Meta sends a `leadgen` webhook change to our "
        "server. The app then calls the Graph API leads edge with the Page "
        "access token to retrieve that lead's submitted fields (name, email, "
        "phone, custom questions) and writes the lead into our CRM "
        "(EspoCRM) for follow-up. The permission is required because lead "
        "PII is not included in the webhook payload — it must be fetched "
        "per leadgen_id."
    ),
    "allowed_usage": (
        "1. A Meta user logs into SocialAuto with Facebook Login for "
        "Business and grants the app access to their Facebook Page.\n"
        "2. The Page is subscribed to the app's webhook; Meta delivers a "
        "leadgen change whenever someone submits a lead form on an ad "
        "running under that Page.\n"
        "3. Our server receives the change at "
        f"{WEBHOOK_URL} (GET verification + POST events, HMAC-verified).\n"
        "4. For each leadgen_id we call "
        "GET /{leadgen_id}?fields=field_data,created_time,ad_id,form_id "
        "with the Page access token — this is the leads_retrieval call.\n"
        "5. The lead's answers are upserted into EspoCRM as a Lead record "
        "with source attribution (ad_id/form_id), then the sales team "
        "follows up. The app never stores the Facebook password and never "
        "shares lead data with third parties."
    ),
    "data_handling": (
        "Data accessed: lead form submissions only (name, email, phone, "
        "and the custom questions defined on our own lead ads). No "
        "passwords, no payment data, no friends list. Data is processed "
        "on our own infrastructure (Cloudflare-tunnelled VPS in Greece, "
        "EU). Lead records live in our self-hosted EspoCRM; retention is "
        "indefinite while the business relationship is active and deleted "
        "on request via " + DATA_DELETION_URL + ". Sub-processors: "
        "Cloudflare (CDN/tunnel only — no lead-data processing), Resend "
        "(transactional email of the same lead data to our own staff). "
        "The app is the data controller; users can revoke access at any "
        "time in their Facebook settings, which stops all API access "
        "immediately."
    ),
    "reviewer_instructions": (
        "1. Log into https://social.cloudless.gr with the test account "
        "credentials provided in the access-code field.\n"
        "2. Go to Settings → Accounts → Facebook and click Connect — the "
        "Facebook Login for Business dialog appears; grant the requested "
        "permissions.\n"
        "3. To see lead retrieval in action, open the Cloudless Facebook "
        "Page and submit the test lead form linked in the screencast "
        "(a sample lead ad is included). Within seconds the webhook fires "
        "and a Lead record appears in the CRM under CRM → Leads, showing "
        "the submitted name/email/answers.\n"
        "4. Alternatively, trigger a test delivery from the Page's lead "
        "forms list — any new submission exercises the same path."
    ),
    "screencast_requirements": (
        "Required: ONE unique video showing, end to end —\n"
        "  a) Facebook Login for Business consent screen granting "
        "leads_retrieval,\n"
        "  b) a lead form submission on the Page,\n"
        "  c) the webhook firing (server log or UI event),\n"
        "  d) the lead record appearing in EspoCRM with the submitted "
        "fields.\n"
        "The existing cloudless-screencast.mp4 covers the app surface but "
        "NOT the leadgen flow — a new recording is required for this "
        "permission. Capture at 1280x720+, English UI, with captions for "
        "any non-English screens. Generate frames in social-api with "
        "ffmpeg (see developer-apps-ops/SKILL.md 'Screencast generation')."
    ),
    "known_blockers": (
        "- Verification step is BLOCKED: business verification cannot "
        "connect the cloudless.gr portfolio — permanent advertising "
        "restriction on the personal FB account (ad account "
        "657781691826702, disabled 2021-01-24, 'too much time has "
        "passed' = unreviewable). Business verification is NOT required "
        "for leads_retrieval per Meta docs, but the wizard may still "
        "gate submission on it.\n"
        "- leads_retrieval is NOT yet added to the draft — add it under "
        "the Facebook Login for Business use case: App Dashboard → Use "
        "cases → Facebook Login for Business → Review → Permissions.\n"
        "- Reviewer test account reviewer@cloudless.gr (EDITOR on admin "
        "team) — credentials live in the submission access-code field; "
        "DELETE the account after review concludes.\n"
        "- Temporary Cloudflare Access bypass on social.cloudless.gr "
        "must be applied before submitting (reviewers can't pass SSO) "
        "and reverted after — see cloudflare-ops skill."
    ),
}


def main() -> int:
    as_json = "--json" in sys.argv
    if as_json:
        print(json.dumps({
            "app_id": APP_ID, "app": APP_NAME, "submission_id": SUBMISSION_ID,
            "business_id": BUSINESS_ID, **FIELDS,
        }, indent=2))
        return 0
    print(f"Meta App Review draft — app {APP_NAME} ({APP_ID})")
    print(f"submission {SUBMISSION_ID} | business {BUSINESS_ID}\n")
    print(f"privacy: {PRIVACY_URL}")
    print(f"data-deletion: {DATA_DELETION_URL}")
    print(f"webhook: {WEBHOOK_URL}\n")
    for key, val in FIELDS.items():
        print(f"===== {key} =====")
        print(val)
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
