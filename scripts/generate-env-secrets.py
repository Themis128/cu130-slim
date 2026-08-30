#!/usr/bin/env python3
"""Generate a development .env file with random, safe defaults.

Real platform credentials (Cloudflare, LinkedIn OAuth, N8N_API_KEY,
GITHUB_PERSONAL_ACCESS_TOKEN, SMTP password) cannot be generated; the script
leaves those empty and prints a checklist.

Usage:
    python3 scripts/generate-env-secrets.py
    python3 scripts/generate-env-secrets.py --output /path/to/.env

WARNING: This overwrites .env. Back it up first if it contains real secrets.
"""

import argparse
import re
import secrets
import sys
from pathlib import Path


def token(n: int = 32) -> str:
    return secrets.token_hex(n)


def password(n: int = 24) -> str:
    return secrets.token_urlsafe(n)


# Variables to replace with random dev values.
GENERATED = {
    "JWT_SECRET_KEY": token(32),
    "ENCRYPTION_KEY": token(32),
    "SECRET_KEY": token(32),
    "N8N_ENCRYPTION_KEY": token(32),
    "REDIS_PASSWORD": password(24),
    "SOCIAL_POSTGRES_PASSWORD": password(24),
    "POSTGRES_PASSWORD": password(24),
    "METABASE_DB_PASS": password(24),
    "SOCIAL_ADMIN_PASSWORD": password(24),
    "N8N_PASSWORD": password(24),
    "ENV_MANAGER_PASS": password(24),
    "MINIO_ROOT_PASSWORD": password(24),
}


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    example = repo_root / ".env.example"
    if not example.exists():
        print(".env.example not found", file=sys.stderr)
        return 1

    parser = argparse.ArgumentParser(description="Generate a development .env file")
    parser.add_argument("--output", type=Path, default=repo_root / ".env", help="Output .env path")
    args = parser.parse_args()

    text = example.read_text(encoding="utf-8")

    for key, value in GENERATED.items():
        # Replace the first occurrence of KEY=... with the generated value.
        text = re.sub(rf"^{re.escape(key)}=.*$", f"{key}={value}", text, count=1, flags=re.MULTILINE)

    args.output.write_text(text, encoding="utf-8")
    print(f"Wrote generated secrets to {args.output}")

    print("\n# =============================================================================")
    print("# PRODUCTION CREDENTIALS CHECKLIST (fill these manually before deploying)")
    print("# =============================================================================")
    manual = [
        "CLOUDFLARE_API_TOKEN",
        "CLOUDFLARE_ACCOUNT_ID",
        "CLOUDFLARE_EMAIL_API_TOKEN (if EMAIL_PROVIDER=cloudflare)",
        "LINKEDIN_CLIENT_ID",
        "LINKEDIN_CLIENT_SECRET",
        "GITHUB_PERSONAL_ACCESS_TOKEN",
        "N8N_API_KEY (mint in n8n UI → Settings → API)",
        "SMTP_PASSWORD",
        "GROQ_API_KEY (optional)",
        "TOGETHER_API_KEY (optional)",
        "HUGGINGFACE_API_KEY (optional)",
        "OPENAI_API_KEY (optional)",
    ]
    for m in manual:
        print(f"# - {m}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
