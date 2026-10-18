FROM node:22-slim

WORKDIR /app

COPY package.json ./
# patchright pulls its own patched Chromium (--with-deps adds the OS libs);
# xvfb-run provides the virtual display for headed mode.
RUN npm install --production --no-cache && \
    npx patchright install --with-deps chromium && \
    apt-get install -y --no-install-recommends xvfb && \
    rm -rf /var/lib/apt/lists/*

COPY server.js session-recovery.js ./

EXPOSE 9225

# Headed Chromium under Xvfb — LinkedIn fingerprints headless hard (the
# article editor boot-loops on a skeleton page). 1920x1080x24 matches the
# context viewport.
CMD ["xvfb-run", "-a", "-s", "-screen 0 1920x1080x24", "node", "server.js"]
