# Build stage
FROM public.ecr.aws/docker/library/node:22-alpine AS builder

WORKDIR /app

# Install system dependencies and apply OS security patches
RUN apk update && apk upgrade --available

COPY package*.json ./
RUN npm ci

COPY . .
RUN npm run build

FROM public.ecr.aws/docker/library/nginx:alpine

# Install system dependencies and apply OS security patches
RUN apk update && apk upgrade --available

COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 8080

CMD ["nginx", "-g", "daemon off;"]