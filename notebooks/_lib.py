"""Shared helpers for cloudless Jupyter notebooks.

    from _lib import db, slack, espo, dmr, sql
    df = db("SELECT * FROM publish_queue ORDER BY 1 DESC LIMIT 10")
"""
import os, json
import pandas as pd
import psycopg2

DATABASE_URL = os.environ["DATABASE_URL"]
SLACK_TOKEN = None  # filled lazily from /app env or secrets


def db(sql_text: str, params=None) -> pd.DataFrame:
    """Query the SocialAuto Postgres into a DataFrame."""
    url = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    with psycopg2.connect(url) as conn:
        return pd.read_sql(sql_text, conn, params=params)


def sql():
    """Load %sql magic bound to the app DB."""
    from IPython import get_ipython
    ip = get_ipython()
    ip.run_line_magic("load_ext", "sql")
    ip.run_line_magic("config", f'SqlMagic.engine = None')
    ip.run_line_magic("sql", f'postgresql://{DATABASE_URL.split("@")[1]}')
    return "sql magic ready — use %%sql or %sql"


def slack(text: str) -> bool:
    """Post to the Slack ops channel directly via bot token (env-injected)."""
    import httpx
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    channel = os.environ.get("SLACK_CHANNEL_ID", "")
    if not token or not channel:
        print("slack: SLACK_BOT_TOKEN/SLACK_CHANNEL_ID not set")
        return False
    r = httpx.post("https://slack.com/api/chat.postMessage",
                   headers={"Authorization": f"Bearer {token}"},
                   json={"channel": channel, "text": text}, timeout=15)
    ok = r.json().get("ok", False)
    if not ok: print("slack err:", r.text[:200])
    return bool(ok)


async def espo(entity: str, where: list | None = None, select: list | None = None, max_size: int = 200):
    """EspoCRM GET via list_all-style searchParams — returns list of dicts."""
    import httpx
    base = os.environ["ESPOCRM_BASE_URL"].rstrip("/")
    key = os.environ["ESPOCRM_API_KEY"]
    search = {"maxSize": max_size}
    if where: search["where"] = where
    if select: search["select"] = select
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{base}/api/v1/{entity}",
                        headers={"X-Api-Key": key},
                        params={"searchParams": json.dumps(search)})
        r.raise_for_status()
        return r.json().get("list", [])


async def dmr(prompt: str, model: str | None = None) -> str:
    """Call DMR local inference (qwen3)."""
    import httpx
    base = os.environ.get("DMR_BASE_URL", "http://host.docker.internal:12435/engines/v1")
    model = model or os.environ.get("DMR_MODEL", "ai/llama3.2")
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(f"{base}/chat/completions",
                         json={"model": model,
                               "messages": [{"role": "user", "content": prompt}],
                               "max_tokens": 600})
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
