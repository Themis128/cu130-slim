#!/usr/bin/env python3
# apply_acl.py — merge tag:authentik-admin into the live tailnet ACL via API v2.
# Env: TAILSCALE_API_KEY (or ~/.config/secrets/tailscale-api).
import json,os,sys,urllib.request
def key():
    if os.environ.get("TAILSCALE_API_KEY"): return os.environ["TAILSCALE_API_KEY"]
    for l in open(os.path.expanduser("~/.config/secrets/tailscale-api")):
        if "TAILSCALE_API_KEY=" in l: return l.split("=",1)[1].strip()
    return ""
K=key() or sys.exit("NO TAILSCALE_API_KEY")
def api(p,m="GET",b=None):
    r=urllib.request.Request("https://api.tailscale.com/api/v2"+p,method=m,
        data=json.dumps(b).encode() if b is not None else None,
        headers={"Authorization":"Bearer "+K,"Content-Type":"application/json"})
    return urllib.request.urlopen(r,timeout=20).read()
TN="themis128.github"
acl=json.loads(api(f"/tailnet/{TN}/acl"))
acl.setdefault("tagOwners",{})["tag:authentik-admin"]=["autogroup:admin"]
rule={"action":"accept","src":["tag:authentik-admin"],
      "dst":["100.74.191.58:22,443,6443,30910","100.95.117.84:22,443,6443,30910"]}
acls=acl.setdefault("acls",[])
if not any(x.get("src")==rule["src"] for x in acls): acls.append(rule)
api(f"/tailnet/{TN}/acl","POST",acl)
print("ACL-APPLIED")
