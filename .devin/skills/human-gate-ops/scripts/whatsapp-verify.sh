#!/usr/bin/env bash
# Finish WhatsApp phone registration once the owner supplies the SMS code.
#   ./whatsapp-verify.sh 123456 [PIN]
# Runs inside social-api so it sees WHATSAPP_* env; prints only statuses.
set -eu
CODE="${1:?usage: whatsapp-verify.sh CODE [PIN]}"
PIN="${2:-${WHATSAPP_PIN:-}}"
cd /home/tbaltzakis/cu130-slim
docker compose exec -T social-api python - "$CODE" "$PIN" <<'PYEOF'
import asyncio, sys
code, pin = sys.argv[1], (sys.argv[2] or None)
async def main():
    from app.services.whatsapp_api import get_whatsapp_client
    c = await get_whatsapp_client()
    r = await c.verify_code(code=code)
    print("verify_code:", r)
    if pin:
        r2 = await c.register(pin=pin)
        print("register:", r2)
asyncio.run(main())
PYEOF
