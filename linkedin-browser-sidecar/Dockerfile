FROM mcr.microsoft.com/playwright:v1.62.1-noble

WORKDIR /app

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

COPY package.json ./
RUN npm install --production --no-cache && npx patchright install chromium

COPY server.js ./

EXPOSE 9225

CMD ["node", "server.js"]
