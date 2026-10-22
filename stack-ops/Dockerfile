FROM python:3.12-alpine
RUN apk add --no-cache docker-cli docker-cli-compose
WORKDIR /app
COPY stack_ops.py services.json ./
EXPOSE 8787
CMD ["python", "-u", "stack_ops.py"]
