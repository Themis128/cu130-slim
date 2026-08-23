#!/bin/bash

# Stable Diffusion 3.5 NIM Setup Script
# This script helps you set up the NVIDIA NIM for Stable Diffusion 3.5

set -e

echo "🚀 Setting up Stable Diffusion 3.5 NIM for Image Generation"
echo "========================================================="
echo ""

# Check if .env file exists
if [ ! -f .env ]; then
    echo "❌ .env file not found. Please copy .env.example to .env and configure it first."
    echo "   Run: cp .env.example .env"
    exit 1
fi

# Source the .env file
export $(cat .env | grep -v '^#' | xargs)

# Check for required credentials
if [ -z "$NGC_API_KEY" ]; then
    echo "❌ NGC_API_KEY not set in .env file."
    echo "   Please get your API key from: https://build.nvidia.com/"
    echo "   Add it to your .env file: NGC_API_KEY=your_key_here"
    exit 1
fi

if [ -z "$HF_TOKEN" ]; then
    echo "❌ HF_TOKEN not set in .env file."
    echo "   Please get your Hugging Face token from: https://huggingface.co/settings/tokens"
    echo "   Make sure it has Read access to gated repositories"
    echo "   Add it to your .env file: HF_TOKEN=your_token_here"
    exit 1
fi

echo "✅ Required credentials found in .env file"
echo ""

# Create nim-cache directory if it doesn't exist
if [ ! -d "nim-cache" ]; then
    echo "📁 Creating nim-cache directory..."
    mkdir -p nim-cache
    chmod 777 nim-cache
    echo "✅ nim-cache directory created"
else
    echo "✅ nim-cache directory already exists"
fi

echo ""
echo "🔐 Logging in to NVIDIA NGC..."
echo "$NGC_API_KEY" | docker login nvcr.io --username '$oauthtoken' --password-stdin
if [ $? -eq 0 ]; then
    echo "✅ Successfully logged in to NVIDIA NGC"
else
    echo "❌ Failed to login to NVIDIA NGC. Please check your NGC_API_KEY."
    exit 1
fi

echo ""
echo "🎉 Setup complete! You can now start the Stable Diffusion 3.5 NIM:"
echo ""
echo "   docker-compose up -d stable-diffusion-nim"
echo ""
echo "The NIM will take a few minutes to initialize and download the model."
echo "You can check the logs with:"
echo "   docker-compose logs -f stable-diffusion-nim"
echo ""
echo "Once initialized, you'll see 'Pipeline warmup: start/done' in the logs."
echo "Then the NIM will be ready for image generation."
