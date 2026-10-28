# Cloud Agent base image for the Social Automation Platform.
# Installs stable system dependencies once; repo checkout + install.sh refresh
# Python/Node project deps on top. Do not COPY the repository into this image.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
        sudo \
        gnupg \
        build-essential \
        python3 \
        python3-venv \
        python3-dev \
        python3-pip \
        libpq-dev \
        libjpeg-dev \
        zlib1g-dev \
        libde265-dev \
        libheif-dev \
        postgresql \
        postgresql-contrib \
        redis-server \
    && rm -rf /var/lib/apt/lists/*

# Node.js 22 (matches the frontend engines requirement)
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Non-root user used by Cloud Agents (passwordless sudo for service startup)
RUN if ! id -u ubuntu >/dev/null 2>&1; then useradd -m -s /bin/bash ubuntu; fi \
    && echo 'ubuntu ALL=(ALL) NOPASSWD:ALL' >/etc/sudoers.d/ubuntu \
    && chmod 0440 /etc/sudoers.d/ubuntu

USER ubuntu
WORKDIR /home/ubuntu
