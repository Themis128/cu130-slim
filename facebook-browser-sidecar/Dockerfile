FROM mcr.microsoft.com/playwright:v1.48.0-jammy

WORKDIR /app

COPY package.json ./
RUN npm install --production --no-cache && npx patchright install chromium

COPY server.js ./

EXPOSE 9226

CMD ["node", "server.js"]
