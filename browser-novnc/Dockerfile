FROM python:3.11-slim

# ── System deps: Xvfb (virtual display), x11vnc, Fluxbox, noVNC, websockify,
#    PulseAudio + GStreamer (audio forwarding via WebSocket) ──
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
    pulseaudio \
    pulseaudio-utils \
    gstreamer1.0-tools \
    gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad \
    gstreamer1.0-libav \
    && rm -rf /var/lib/apt/lists/*

# ── Patchright (undetected Playwright) + Chromium + FastAPI bridge ──
# Patchright is a patched Playwright drop-in that removes the CDP
# Runtime.enable leak and automation flags that Meta/TikTok/Instagram use
# to silently abort mutations or revoke sessions.
RUN pip install --no-cache-dir patchright playwright httpx fastapi uvicorn && \
    patchright install chromium && \
    patchright install-deps chromium

# ── Node.js 22 (for npx playwright, codegen, MCP) ──
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/* && \
    npm install -g playwright @playwright/test

WORKDIR /app

# Copy scripts
COPY extract-cookies.py /app/extract-cookies.py
COPY browser-bridge.py /app/browser-bridge.py
COPY audio-proxy.sh /app/audio-proxy.sh
COPY vnc-audio.html /usr/share/novnc/vnc-audio.html
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf
COPY pulse-config/default.pa /etc/pulse/default.pa
COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh /app/extract-cookies.py /app/browser-bridge.py /app/audio-proxy.sh

# Cookie output directory (shared volume)
RUN mkdir -p /app/cookies

ENV DISPLAY=:99
ENV SCREEN_WIDTH=1280
ENV SCREEN_HEIGHT=800
ENV SCREEN_DEPTH=24
ENV VNC_PORT=5900
ENV NOVNC_PORT=6080
ENV BRIDGE_PORT=9223
ENV AUDIO_WS_PORT=6081
ENV PYTHONUNBUFFERED=1

EXPOSE 6080 5900 9223 6081

CMD ["/app/start.sh"]
