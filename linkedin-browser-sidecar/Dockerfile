FROM mcr.microsoft.com/playwright:v1.62.1-noble

WORKDIR /app

COPY package.json ./
RUN npm install --production --no-cache && npx patchright install chromium

COPY server.js session-recovery.js ./

EXPOSE 9225

# Headed Chromium under Xvfb — LinkedIn fingerprints headless hard (the
# article editor boot-loops on a skeleton page). 1920x1080x24 matches the
# context viewport.
CMD ["xvfb-run", "-a", "-s", "-screen 0 1920x1080x24", "node", "server.js"]
