#!/usr/bin/env python3
"""Unload running models from memory to free VRAM.
Usage: dmr-unload.py [--all | model1 model2 ... | --backend <backend>]"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import dmr_get, dmr_post, docker_model  # noqa: E402

args = sys.argv[1:]
api_ok = dmr_get("/inference/unload") is not None

if args and args[0] == "--all":
    print("Unloading ALL running models...")
    if api_ok:
        print(json.dumps(dmr_post("/inference/unload", {"all": True}), indent=2))
    else:
        docker_model("unload", "--all")
elif args and args[0] == "--backend":
    if len(args) < 2:
        sys.exit("Usage: dmr-unload.py --backend <backend>")
    print(f"Unloading all models for backend: {args[1]}...")
    if api_ok:
        print(json.dumps(dmr_post("/inference/unload", {"backend": args[1]}),
                         indent=2))
    else:
        docker_model("unload", "--backend", args[1])
elif args:
    print(f"Unloading models: {' '.join(args)}")
    if api_ok:
        print(json.dumps(dmr_post("/inference/unload", {"models": args}),
                         indent=2))
    else:
        docker_model("unload", *args)
else:
    print("Usage: dmr-unload.py [--all | model1 model2 ... | --backend <backend>]")
    sys.exit(1)
print("Done.")
