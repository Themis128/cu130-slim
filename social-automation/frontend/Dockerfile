# Build stage
FROM public.ecr.aws/docker/library/node:22-alpine AS builder

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
FROM public.ecr.aws/docker/library/node:22-alpine AS runner

WORKDIR /app

# Install system dependencies and apply OS security patches
RUN apk update && apk upgrade --available

ENV NODE_ENV=production

# Copy built assets (standalone output)
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static

# novnc-server.cjs is the container's real entrypoint (compose `command`).
# Standalone output never includes it — the launcher sits outside the Next.js
# module graph — so ship it explicitly alongside the http-proxy package and
# its three runtime deps it needs at runtime.
COPY novnc-server.cjs ./novnc-server.cjs
COPY --from=builder /app/node_modules/http-proxy ./node_modules/http-proxy
COPY --from=builder /app/node_modules/eventemitter3 ./node_modules/eventemitter3
COPY --from=builder /app/node_modules/follow-redirects ./node_modules/follow-redirects
COPY --from=builder /app/node_modules/requires-port ./node_modules/requires-port

# Remove bundled npm and test-only packages from the runtime image; the
# standalone server does not need them and they carry CVE-heavy dependencies.
RUN rm -rf /usr/local/lib/node_modules/npm /usr/local/bin/npm /usr/local/bin/npx && \
    find /app -type d \( -name 'playwright*' -o -name 'tar' -o -name 'sigstore' \) -prune -exec rm -rf {} + 2>/dev/null || true

EXPOSE 8083

# Run Next.js standalone server
CMD ["node", "server.js"]
