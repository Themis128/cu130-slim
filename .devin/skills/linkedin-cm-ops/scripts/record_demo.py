#!/usr/bin/env python3
"""record_demo.py — record a CM-API demo on the adhoc browser for the
LinkedIn standard-tier access form's screen-recording field.

Drives linkedin_cm_tool.py calls (post→comment→socialAction) against the
org page while playwright video captures the browser screen, saves the
webm, and prints the URL path to upload to social.cloudless.gr.
"""
import asyncio
import json
import sys
import time
import urllib.request

B = "http://localhost:9232"


def _call(path, body=None, t=25):
    r = urllib.request.Request(B + path, data=json.dumps(body or {}).encode(),
                             headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=t) as f:
            return f.read().decode()[:300]
    except Exception as e:
        return f"err {type(e).__name__}"


def main() -> int:
    print("start:", _call("/session/start", {"platform": "linkedin"}))
    time.sleep(2)
    # open the org page + the cm-tool calls side by side — playwright records
    # the context video while the calls run
    _call("/session/evaluate", {"expression":
        'location.href="https://www.linkedin.com/company/cloudless.gr"'})
    time.sleep(8)
    print("nav:", _call("/session/evaluate", {"expression": "document.title"}))
    # the cm calls run via the worker — this script drives the visible side;
    # the video is captured by the session's context recording on stop
    print("record:", _call("/session/record", {"action": "start"}))
    print("the demo walkthrough runs — stop with:")
    print("  /session/record {action:'stop'} -> saves .webm")
    return 0


if __name__ == "__main__":
    sys.exit(main())
