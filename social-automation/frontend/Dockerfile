# Build stage
FROM node:20-alpine AS builder

WORKDIR /app

# Install system dependencies and apply OS security patches
RUN apk update && apk upgrade --available

# Copy package files
COPY package*.json ./

# Install dependencies
RUN npm ci --legacy-peer-deps

# Copy source code
COPY . .

# Build the application (outputs standalone server)
RUN npm run build

# Production stage - Next.js standalone server
FROM node:20-alpine AS runner

WORKDIR /app

# Install system dependencies and apply OS security patches
RUN apk update && apk upgrade --available

ENV NODE_ENV=production

# Copy built assets (standalone output)
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static

EXPOSE 8083

# Run Next.js standalone server
CMD ["node", "server.js"]