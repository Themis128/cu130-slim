#!/usr/bin/env python3
"""Wire X OAuth creds into the SocialAuto stack and generate the reauth URL.

Usage: python wire.py <client_id> <client_secret>
"""
import sys
import subprocess
import re
import time
import urllib.request

REPO = r"C:\Users\tbaltzakis\cu130-slim"
ENV = REPO + r"\.env"

def main(cid, secret):
    with open(ENV, encoding="utf-8") as _f:
        env = _f.read()
    # replace or append the two keys
    for key, val in [("TWITTER_CLIENT_ID", cid), ("TWITTER_CLIENT_SECRET", secret)]:
        if re.search(rf"^{key}=", env, re.M):
            env = re.sub(rf"^{key}=.*", f"{key}={val}", env, flags=re.M)
        else:
            env += f"\n{key}={val}\n"
    with open(ENV, "w", encoding="utf-8") as f:
        f.write(env)
    print("env written")

    subprocess.run(["docker","compose","-f",REPO+"\\docker-compose.yml","up","-d","social-api"], capture_output=True)
    print("social-api restarting...")
    time.sleep(12)

    try:
        url = urllib.request.urlopen("http://localhost:8083/api/v1/oauth/twitter/authorize", timeout=10).read().decode()
        print("authorize response:", url[:200])
    except Exception as e:
        print("authorize call failed:", e)
        print("check social-api logs: docker logs social-api --tail 30")

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: wire.py <client_id> <client_secret>"); sys.exit(1)
    main(*sys.argv[1:])
