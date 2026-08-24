#!/bin/bash
# Script to initialize n8n API key after n8n starts
# Run this after n8n container is up and running

set -e

N8N_URL="http://localhost:5678"
N8N_USER="admin"
N8N_PASSWORD="secure_password"
API_KEY_LABEL="social-automation-api-key"
API_KEY_EXPIRY_DAYS=365

echo "Waiting for n8n to be ready..."
until curl -s -f -u "${N8N_USER}:${N8N_PASSWORD}" "${N8N_URL}/healthz" > /dev/null 2>&1; do
    echo "  n8n not ready yet, waiting..."
    sleep 5
done

echo "n8n is ready!"

# Check if API key already exists
echo "Checking for existing API key..."
EXISTING_KEYS=$(curl -s -u "${N8N_USER}:${N8N_PASSWORD}" \
    -H "Accept: application/json" \
    "${N8N_URL}/api/v1/user/api-keys" 2>/dev/null || echo "[]")

if echo "$EXISTING_KEYS" | grep -q '"label":"'"${API_KEY_LABEL}"'"'; then
    echo "API key '${API_KEY_LABEL}' already exists"
    EXISTING_KEY=$(echo "$EXISTING_KEYS" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for key in data.get('data', []):
    if key.get('label') == '${API_KEY_LABEL}':
        print(key.get('key', ''))
        break
")
    if [ -n "$EXISTING_KEY" ]; then
        echo "Existing API key: ${EXISTING_KEY}"
        echo "N8N_API_KEY=${EXISTING_KEY}"
        exit 0
    fi
fi

echo "Creating new API key..."

# Create API key with required scopes for workflow management
# Scopes needed: workflow:create, workflow:read, workflow:execute, workflow:list, workflow:update, workflow:delete, workflow:activate
CREATE_RESPONSE=$(curl -s -X POST \
    -u "${N8N_USER}:${N8N_PASSWORD}" \
    -H "Content-Type: application/json" \
    -H "Accept: application/json" \
    -d "{
        \"label\": \"${API_KEY_LABEL}\",
        \"expiresAt\": \"$(date -d \"+${API_KEY_EXPIRY_DAYS} days\" -Iseconds)\",
        \"scopes\": [
            \"workflow:create\",
            \"workflow:read\",
            \"workflow:execute\",
            \"workflow:list\",
            \"workflow:update\",
            \"workflow:delete\",
            \"workflow:activate\"
        ]
    }" \
    "${N8N_URL}/api/v1/user/api-keys" 2>/dev/null)

echo "Create response: ${CREATE_RESPONSE}"

# Extract the API key from response
API_KEY=$(echo "$CREATE_RESPONSE" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    # Response format varies, try different paths
    if 'data' in data and 'key' in data['data']:
        print(data['data']['key'])
    elif 'key' in data:
        print(data['key'])
    elif 'apiKey' in data:
        print(data['apiKey'])
except:
    pass
" 2>/dev/null)

if [ -z "$API_KEY" ]; then
    echo "ERROR: Failed to create API key"
    echo "Response: ${CREATE_RESPONSE}"
    exit 1
fi

echo "Successfully created API key: ${API_KEY}"
echo ""
echo "Add this to your .env file:"
echo "N8N_API_KEY=${API_KEY}"
echo ""
echo "Then restart social-api and social-worker containers:"
echo "  docker-compose restart social-api social-worker"

# Optionally update .env file automatically
if [ -f "/home/tbaltzakis/ComfyUI-Docker/cu130-slim/.env" ]; then
    echo "Updating .env file..."
    sed -i "s/^# N8N_API_KEY=.*/N8N_API_KEY=${API_KEY}/" /home/tbaltzakis/ComfyUI-Docker/cu130-slim/.env
    echo ".env updated. Restart containers to apply."
fi