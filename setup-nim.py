#!/usr/bin/env python3
"""Stable Diffusion 3.5 NIM Setup Script.
Helps you set up the NVIDIA NIM for Stable Diffusion 3.5.
Usage: setup-nim.py"""

import subprocess
import sys
from pathlib import Path

print("🚀 Setting up Stable Diffusion 3.5 NIM for Image Generation")
print("=========================================================\n")

env_file = Path(".env")
if not env_file.is_file():
    print("❌ .env file not found. Please copy .env.example to .env "
          "and configure it first.")
    print("   Run: cp .env.example .env")
    sys.exit(1)

env = {}
for line in env_file.read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()

if not env.get("NGC_API_KEY"):
    print("❌ NGC_API_KEY not set in .env file.")
    print("   Please get your API key from: https://build.nvidia.com/")
    print("   Add it to your .env file: NGC_API_KEY=your_key_here")
    sys.exit(1)

if not env.get("HF_TOKEN"):
    print("❌ HF_TOKEN not set in .env file.")
    print("   Please get your Hugging Face token from: "
          "https://huggingface.co/settings/tokens")
    print("   Make sure it has Read access to gated repositories")
    print("   Add it to your .env file: HF_TOKEN=your_token_here")
    sys.exit(1)

print("✅ Required credentials found in .env file\n")

cache = Path("nim-cache")
if not cache.is_dir():
    print("📁 Creating nim-cache directory...")
    cache.mkdir(parents=True)
    cache.chmod(0o777)
    print("✅ nim-cache directory created")
else:
    print("✅ nim-cache directory already exists")

print("\n🔐 Logging in to NVIDIA NGC...")
r = subprocess.run(
    ["docker", "login", "nvcr.io", "--username", "$oauthtoken",
     "--password-stdin"], input=env["NGC_API_KEY"].encode())
if r.returncode == 0:
    print("✅ Successfully logged in to NVIDIA NGC")
else:
    print("❌ Failed to login to NVIDIA NGC. "
          "Please check your NGC_API_KEY.")
    sys.exit(1)

print("""
🎉 Setup complete! You can now start the Stable Diffusion 3.5 NIM:

   docker compose up -d stable-diffusion-nim

The NIM will take a few minutes to initialize and download the model.
You can check the logs with:
   docker compose logs -f stable-diffusion-nim

Once initialized, you'll see 'Pipeline warmup: start/done' in the logs.
Then the NIM will be ready for image generation.""")
