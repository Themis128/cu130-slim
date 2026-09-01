FROM python:3.11-slim

# ── System deps: Xvfb (virtual display), x11vnc, Fluxbox, noVNC, websockify ──
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb \
    x11vnc \
    fluxbox \
    novnc \
    websockify \
    socat \
    netcat-openbsd \
    procps \
    curl \
    git \
    supervisor \
    && rm -rf /var/lib/apt/lists/*

# ── Playwright + Chromium (Python) + FastAPI bridge ──
RUN pip install --no-cache-dir playwright httpx fastapi uvicorn && \
    playwright install chromium && \
    playwright install-deps chromium

# ── Node.js 22 (for npx playwright, codegen, MCP) ──
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/* && \
    npm install -g playwright @playwright/test

WORKDIR /app

# Copy scripts
COPY extract-cookies.py /app/extract-cookies.py
COPY browser-bridge.py /app/browser-bridge.py
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf
COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh /app/extract-cookies.py /app/browser-bridge.py

# Cookie output directory (shared volume)
RUN mkdir -p /app/cookies

ENV DISPLAY=:99
ENV SCREEN_WIDTH=1280
ENV SCREEN_HEIGHT=800
ENV SCREEN_DEPTH=24
ENV VNC_PORT=5900
ENV NOVNC_PORT=6080
ENV BRIDGE_PORT=9223
ENV PYTHONUNBUFFERED=1

EXPOSE 6080 5900 9223

CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
