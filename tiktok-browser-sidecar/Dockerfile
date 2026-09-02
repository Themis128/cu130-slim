FROM mcr.microsoft.com/playwright:v1.48.0-jammy

WORKDIR /app

COPY package.json ./
RUN npm install --production --no-cache

COPY server.js ./

EXPOSE 9224

CMD ["node", "server.js"]
