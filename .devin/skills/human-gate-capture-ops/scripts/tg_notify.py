#!/usr/bin/env python3
"""tg_notify — one-shot Telegram to the ops channel."""
import subprocess
import sys
MSG = sys.argv[1] if len(sys.argv) > 1 else "gate note"
script = """
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select
from app.models import SocialAccount
from app.core.config import get_settings
from app.core.security import decrypt_token
from app.services.telegram_api import TelegramAPIClient
async def go():
    eng = create_async_engine(get_settings().DATABASE_URL)
    S = sessionmaker(eng, class_=AsyncSession)
    async with S() as db:
        acc = (await db.execute(select(SocialAccount).where(
            SocialAccount.platform=='telegram',
            SocialAccount.status=='active').limit(1))).scalar_one()
        enc = acc.meta_data.get('bot_token_enc')
        tok = decrypt_token(enc.encode() if isinstance(enc,str) else enc) \\
            if enc else decrypt_token(acc.access_token_enc)
        await TelegramAPIClient(tok).send_message("-1004436942504", %r)
asyncio.run(go())
""" % MSG
subprocess.run(["docker","exec","-i","social-api","python","-"],
               input=script, capture_output=True, text=True, timeout=60)
