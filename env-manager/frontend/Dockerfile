# Build stage
# Base images overridable via compose args (mirror.gcr.io 429s).
ARG NODE_IMAGE=mirror.gcr.io/library/node:22-alpine
ARG NGINX_IMAGE=mirror.gcr.io/library/nginx:alpine
FROM ${NODE_IMAGE} AS builder

WORKDIR /app

# Install system dependencies and apply OS security patches
RUN apk update && apk upgrade --available

COPY package*.json ./
RUN npm ci

COPY . .
RUN npm run build

FROM ${NGINX_IMAGE}

# Install system dependencies and apply OS security patches
RUN apk update && apk upgrade --available

COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 8080

CMD ["nginx", "-g", "daemon off;"]