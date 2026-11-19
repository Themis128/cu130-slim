FROM subzeroid/aiograpi-rest:latest

# Upgrade aiograpi — the upstream image ships 1.0.9 which is too old for
# current Instagram API requirements. >=2.1.19 is required: aiograpi
# <=1.12.x calls the retired qe/sync/ key-exchange endpoint and crashes
# password login with int(None) (ig-set-password-encryption-key-id
# header missing); 2.1.19 uses the signed launcher/sync/ flow.
RUN pip install --no-cache-dir --upgrade \
    "aiograpi>=2.1.19" \
    socksio \
    "httpx[socks]"
