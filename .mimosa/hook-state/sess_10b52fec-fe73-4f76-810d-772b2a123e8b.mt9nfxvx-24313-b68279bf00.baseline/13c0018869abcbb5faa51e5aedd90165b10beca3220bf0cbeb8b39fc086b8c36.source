#!/bin/bash
set -e

PAYLOAD_FILE="payload.json"
COMFYUI_URL="http://localhost:8000"

# Enqueue the prompt
response=$(curl -s -X POST "$COMFYUI_URL/prompt" -H "Content-Type: application/json" -d @"$PAYLOAD_FILE")
prompt_id=$(echo "$response" | python3 -c "import sys, json; print(json.load(sys.stdin)['prompt_id'])")
echo "Enqueued prompt with ID: $prompt_id"

# Wait for completion
max_attempts=6
attempt=1
while [ $attempt -le $max_attempts ]; do
    sleep 5
    history=$(curl -s "$COMFYUI_URL/history/$prompt_id")
    if [ -n "$history" ] && [ "$history" != "{}" ]; then
        # Extract outputs for node 3 (SaveImage)
        filename=$(echo "$history" | python3 -c "
import sys, json
data=json.load(sys.stdin)
outputs = data.get('$prompt_id', {}).get('outputs', {})
if outputs:
    node_3_outputs = outputs.get('3', {})
    images = node_3_outputs.get('images', [])
    if images:
        print(images[0].get('filename'))
        ")
        if [ -n "$filename" ]; then
            echo "Generated image filename: $filename"
            # Check if the file exists in the output directory
            if [ -f "storage-user/output/$filename" ]; then
                echo "Image successfully generated and saved to storage-user/output/$filename"
                exit 0
            else
                echo "Error: File not found in storage-user/output/$filename"
                exit 1
            fi
        fi
    fi
    echo "Attempt $attempt: Waiting for image to be generated..."
    attempt=$((attempt+1))
done

echo "Timeout: Image generation did not complete in the expected time."
exit 1