#!/usr/bin/env python3
"""Run cloudless.gr unit + e2e tests with correct env.
Usage: run-cloudless-tests.py [unit|e2e|all]"""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

mode = sys.argv[1] if len(sys.argv) > 1 else "all"
repo = Path(os.environ.get("CLOUDLESS_REPO", str(Path.home() / "cloudless.gr")))
os.chdir(repo)

# --- Python venv (cloudless.gr) ---
venv_python = repo / ".venv/bin/python"
if not venv_python.exists():
    print(f"==> Creating .venv (needs write access to {repo})")
    if not os.access(repo, os.W_OK):
        st = repo.stat()
        print(f"Repo not writable (owned by uid:{st.st_uid}). Fix with:\n"
              f'  sudo chown -R "$USER:$USER" "{repo}"')
        sys.exit(1)
    subprocess.run([sys.executable, "-m", "venv", ".venv"], check=True)
    subprocess.run([str(venv_python), "-m", "pip", "install", "-q",
                    "-r", "requirements.txt"], check=True)
    subprocess.run([str(venv_python), "-m", "pip", "install", "-q",
                    "ruff", "mypy"], check=True)
print(subprocess.run([str(venv_python), "-V"], capture_output=True,
                     text=True).stdout.strip())

# --- Playwright env ---
env = dict(os.environ)
env["PLAYWRIGHT_BROWSERS_PATH"] = env.get(
    "PLAYWRIGHT_BROWSERS_PATH", str(Path.home() / ".cache/ms-playwright"))
env["LD_LIBRARY_PATH"] = (
    str(Path.home() / ".local/lib/playwright-deps/usr/lib/x86_64-linux-gnu")
    + (":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else ""))

browser = (Path(env["PLAYWRIGHT_BROWSERS_PATH"])
           / "chromium_headless_shell-1234/chrome-headless-shell-linux64"
           / "chrome-headless-shell")
if not browser.exists() or not os.access(browser, os.X_OK):
    print(f"==> Installing Playwright Chromium into "
          f"{env['PLAYWRIGHT_BROWSERS_PATH']}")
    subprocess.run(["pnpm", "exec", "playwright", "install", "chromium"],
                   check=True)

# Strip secrets that pollute "not configured" unit tests
STRIP = ("AUTH_DB CF_R2_ACCESS_KEY_ID CF_R2_SECRET_ACCESS_KEY "
         "R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY CLOUDFLARE_ACCOUNT_ID "
         "CF_ACCOUNT_ID CLOUDFLARE_API_TOKEN CLOUDFLARE_AI_API_TOKEN "
         "WORKERS_AI_ACCOUNT_ID WORKERS_AI_API_TOKEN OPENAI_API_KEY "
         "ANTHROPIC_API_KEY GEMINI_API_KEY GOOGLE_AI_API_KEY "
         "DATALAKE_BUCKET R2_DATALAKE_BUCKET R2_BUCKET_NAME "
         "CF_R2_BUCKET_NAME APP_MEDIA_BUCKET WHATSAPP_ACCESS_TOKEN").split()
unit_env = {k: v for k, v in env.items() if k not in STRIP}


def run_unit() -> None:
    print("==> Vitest (unit)")
    subprocess.run(["pnpm", "test:ci"], env=unit_env, check=True)


def run_e2e() -> None:
    print("==> Freeing :4010 if stale")
    r = subprocess.run(["ss", "-ltn"], capture_output=True, text=True)
    if ":4010" in r.stdout:
        subprocess.run(["pkill", "-f", "next dev -p 4010"],
                       capture_output=True)
        subprocess.run(["pkill", "-f",
                        "playwright test --config=playwright.config.mts"],
                       capture_output=True)
        time.sleep(2)
    print("==> Playwright e2e")
    subprocess.run(["pnpm", "exec", "playwright", "test",
                    "--config=playwright.config.mts",
                    "--workers=2", "--reporter=line"], env=env, check=True)


if mode == "unit":
    run_unit()
elif mode == "e2e":
    run_e2e()
elif mode == "all":
    run_unit()
    run_e2e()
else:
    print(f"Usage: {sys.argv[0]} [unit|e2e|all]")
    sys.exit(2)
