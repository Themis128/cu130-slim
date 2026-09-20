FROM mcr.microsoft.com/playwright:v1.55.1-jammy

WORKDIR /app

COPY package.json ./
RUN npm install --production --no-cache

COPY server.js ./

EXPOSE 9224

CMD ["node", "server.js"]
