#!/usr/bin/env python3
"""Verify a media URL is publicly reachable for TikTok PULL_FROM_URL.
Usage: verify-media-url.py"""

import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import repo_root, request, social_api  # noqa: E402


def probe(url: str) -> tuple[int, int, str]:
    """Return (http_code, size, redirect_url)."""
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    opener = urllib.request.build_opener(NoRedirect)
    try:
        resp = opener.open(url, timeout=15)
        return resp.status, len(resp.read()), ""
    except urllib.error.HTTPError as e:
        return e.code, 0, e.headers.get("Location", "")
    except Exception:
        return 0, 0, ""


api, token = social_api()

print("=== Tunnel URL ===")
r = subprocess.run(
    ["docker", "compose", "exec", "-T", "social-api",
     "cat", "/run/tunnel/url"],
    cwd=repo_root(), capture_output=True, text=True)
tunnel_url = r.stdout.strip()
if not tunnel_url:
    print("No tunnel URL found. Cloudflare tunnel may not be running.")
    sys.exit(1)
print(f"Tunnel: {tunnel_url}")

print("\n=== Health Check ===")
code, _, _ = probe(f"{tunnel_url}/health")
print(f"GET {tunnel_url}/health → {code}")

print("\n=== Media URL Test ===")
assets = request("GET", f"{api}/api/v1/media/assets?limit=1", token=token)
items = assets if isinstance(assets, list) else assets.get("items", assets.get("assets", []))
storage_path = items[0]["storage_path"] if items else ""
if not storage_path:
    print("No media assets found in the library.")
    sys.exit(1)

media_url = f"{tunnel_url}/api/v1/media/view?path={storage_path}"
print(f"Testing: {media_url}")
code, size, redirect = probe(media_url)
print(f"  HTTP {code} | Size: {size} bytes")

if code == 200 and size > 100:
    print("\n✓ Media URL is publicly reachable.")
    print("  TikTok PULL_FROM_URL will work IF the domain is verified.")
    print("  Check verification status at:")
    print("  https://developers.tiktok.com → Cloudless app → URL properties")
else:
    print("\n✗ Media URL is NOT reachable or returned an error.")
    print("  Check Cloudflare tunnel and social-api container.")

print("\n=== HTTPS / No-Redirect Check ===")
if not redirect:
    print("✓ No redirect — TikTok requirement satisfied.")
else:
    print(f"✗ URL redirects to: {redirect}")
    print("  TikTok requires media URLs that do not redirect.")
