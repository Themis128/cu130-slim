FROM node:22-slim

WORKDIR /app

COPY package.json ./
RUN npm install --production --no-cache && npx patchright install --with-deps chromium

COPY server.js ./

EXPOSE 9226

CMD ["node", "server.js"]
