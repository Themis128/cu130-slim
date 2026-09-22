FROM mcr.microsoft.com/playwright:v1.62.1-noble

WORKDIR /app

COPY package.json ./
RUN npm install --production --no-cache && npx patchright install chromium

COPY server.js ./

EXPOSE 9225

CMD ["node", "server.js"]
