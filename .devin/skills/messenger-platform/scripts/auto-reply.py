#!/usr/bin/env python3
"""Get or set AI auto-reply configuration for Messenger.
Usage: auto-reply.py <account_id> [--enable | --disable | --prompt "text" | --model "name" | --fallback "text"]"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import request, social_api  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("account_id")
p.add_argument("--enable", action="store_true")
p.add_argument("--disable", action="store_true")
p.add_argument("--prompt")
p.add_argument("--model")
p.add_argument("--fallback")
p.add_argument("--max-tokens", type=int)
a = p.parse_args()

api, token = social_api()
url = f"{api}/api/v1/messenger/{a.account_id}/auto-reply"

if not any([a.enable, a.disable, a.prompt, a.model, a.fallback, a.max_tokens]):
    print("Current auto-reply config:")
    d = request("GET", url, token=token)
    print(json.dumps(d, indent=2, ensure_ascii=False))
    raise SystemExit(0)

config = request("GET", url, token=token)
if a.enable:
    config["enabled"] = True
if a.disable:
    config["enabled"] = False
if a.prompt:
    config["system_prompt"] = a.prompt
if a.model:
    config["model"] = a.model
if a.fallback:
    config["fallback_text"] = a.fallback
if a.max_tokens:
    config["max_tokens"] = a.max_tokens

print("Updating auto-reply config...")
d = request("PUT", url, token=token, data=config)
print(json.dumps(d, indent=2, ensure_ascii=False))
