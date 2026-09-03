FROM subzeroid/aiograpi-rest:latest

# Upgrade aiograpi to the latest version — the upstream image ships 1.0.9
# which is too old for current Instagram API requirements.
RUN pip install --no-cache-dir --upgrade \
    "aiograpi>=1.12.0" \
    socksio \
    "httpx[socks]"
