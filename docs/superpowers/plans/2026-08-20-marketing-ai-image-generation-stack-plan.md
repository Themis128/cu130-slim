# Marketing AI Image Generation Stack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a hybrid‑automation Docker stack for marketing‑focused AI image generation, where ComfyUI handles generation and post‑processing, n8n orchestrates scheduling and approval, and Ollama, Chroma, and Metabase provide LLM assistance, prompt/style storage, and analytics.

**Architecture:** n8n triggers ComfyUI API workflows (text‑to‑image → img2img → upscale → background removal), waits for results, presents a manual approval gate, then posts or queues the image. Optional prompt enhancement via Ollama and style/prompt similarity via Chroma. Metabase logs runs for dashboards.

**Tech Stack:** Docker Compose, n8n, ComfyUI, Ollama (llama3), Chroma, Metabase, shared storage volumes, Python (for any helper scripts), REST APIs.

## Global Constraints
- Use existing docker‑compose.yml services: n8n (5678), ComfyUI (8000), Ollama (11435), Chroma (8001), Metabase (3000)
- Shared storage mounted at ./storage -> /home/user/ComfyUI (models, output, input, custom_nodes, user)
- ComfyUI must expose /prompt and /history endpoints
- n8n must be able to call HTTP endpoints and display images for approval
- All containers restart: unless‑stopped
- No changes to base images unless absolutely necessary (use existing tags)
- Keep GPU allocation for ComfyUI service as already defined
- Maintain existing environment variables (REPLICATE_API_TOKEN, FAL_KEY, CLI_ARGS, etc.)

---
### Task 1: Prepare shared storage directory structure

**Files:**
- Create: `storage/models/` (ensure exists)
- Create: `storage/output/`
- Create: `storage/input/`
- Create: `storage/custom_nodes/`
- Create: `storage/user/`
- Create: `storage/metadata/` (for logs, CSVs, etc.)

**Interfaces:**
- Consumes: None (initial setup)
- Produces: Directory layout that ComfyUI and n8n will read/write

- [ ] **Step 1: Verify storage directories exist**

```bash
ls -la storage/
```

Expected: lists models, output, input, custom_nodes, user, metadata

- [ ] **Step 2: Create missing directories**

```bash
mkdir -p storage/models storage/output storage/input storage/custom_nodes storage/user storage/metadata
```

- [ ] **Step 3: Verify permissions (allow read/write by container users)**

```bash
chmod -R 777 storage
```

- [ ] **Step 4: Commit**

```bash
git add storage/
git commit -m "chore: ensure storage directory structure for shared volumes"
```

### Task 2: Add n8n workflow JSON for the hybrid automation

**Files:**
- Create: `n8n-workflows/marketing-image-generation.json`

**Interfaces:**
- Consumes: None (self‑contained workflow)
- Produces: n8n‑importable workflow that:
  1. Triggers on cron (e.g., every 6 hours) or webhook
  2. Optional: calls Ollama API to enhance prompt
  3. Optional: queries Chroma for similar prompt/style vectors
  4. POSTs workflow JSON to ComfyUI `/prompt`
  5. Polls ComfyUI `/history/{prompt_id}` for outputs
  6. Retrieves image filename from shared storage
  7. Displays image in n8n UI for manual approval
  8. On approval: posts to social media (Twitter example) or saves to queue
  9. Logs run metadata to CSV in storage/metadata/

- [ ] **Step 1: Create n8n workflow skeleton**

```json
{
  "name": "Marketing Image Generation Hybrid",
  "nodes": [
    {
      "parameters": {
        "triggerTimes": [
          {
            "mode": "everyX",
            "value": 6,
            "unit": "hours"
          }
        ]
      },
      "name": "Cron Trigger",
      "type": "n8n-nodes-base.cron",
      "typeVersion": 1,
      "position": [250, 300]
    },
    {
      "parameters": {
        "operation": "generate",
        "prompt": "={{ $json[\"prompt\"] }}",
        "model": "llama3",
        "temperature": 0.7
      },
      "name": "Ollama Prompt Enhance",
      "type": "n8n-nodes-base.ollama",
      "typeVersion": 1,
      "position": [500, 200],
      "continueOnFail": true
    },
    {
      "parameters": {
        "operation": "post",
        "url": "http://comfyui:8000/prompt",
        "jsonParameters": true,
        "options": {
          "bodyContent": {
            "prompt": "={{ $node[\"Ollama Prompt Enhance\"].json[\"response\"] || $json[\"raw_prompt\"] }}",
            // ComfyUI workflow JSON will be injected here later
          }
        }
      },
      "name": "Call ComfyUI API",
      "type": "n8n-nodes-base.httpRequest",
      "typeVersion": 2,
      "position": [750, 200]
    },
    {
      "parameters": {
        "operation": "get",
        "url": "http://comfyui:8000/history/={{ $node[\"Call ComfyUI API\"].json[\"prompt_id\"] }}",
        "responseFormat": "json",
        "retryOnFail": true,
        "retryInterval": 5000,
        "maxRetries": 12
      },
      "name": "Poll ComfyUI History",
      "type": "n8n-nodes-base.httpRequest",
      "typeVersion": 2,
      "position": [1000, 200]
    },
    {
      "parameters": {
        "operation": "binary",
        "propertyName": "data",
        "binaryPropertyName": "image",
        "options": {
          "url": "={{ \"http://comfyui:8000/view?filename=\" + $node[\"Poll ComfyUI History\"].json[\"outputs\"][\"3\"][\"images\"][0][\"filename\"] }}",
          "responseFormat": "blob"
        }
      },
      "name": "Fetch Image",
      "type": "n8n-nodes-base.httpRequest",
      "typeVersion": 2,
      "position": [1250, 200]
    },
    {
      "parameters": {
        "operation": "display",
        "title": "Generated Image – Approve?",
        "annotation": [{"width": 400, "height": 400, "path": "image", "type": "image"}]
      },
      "name": "Manual Approval",
      "type": "n8n-nodes-base.manual",
      "typeVersion": 1,
      "position": [1500, 200]
    },
    {
      "parameters": {
        "operation": "post",
        "url": "={{ $env[\"TWITTER_API_URL\"] }}",
        "authentication": "Bearer Token",
        "token": "={{ $env[\"TWITTER_BEARER_TOKEN\"] }}",
        "options": {
          "bodyContent": {
            "status": "={{ $json[\"caption\"] }}",
            "media_ids": ["={{ $node[\"Fetch Image\"].binary?.image?.id }}"]
          }
        }
      },
      "name": "Post to Twitter",
      "type": "n8n-nodes-base.httpRequest",
      "typeVersion": 2,
      "position": [1750, 100],
      "continueOnFail": true
    },
    {
      "parameters": {
        "operation": "append",
        "path": "={{ \"storage/metadata/run_log.csv\" }}",
        "options": {
          "csv": {
            "fields": [
              {"name": "timestamp", "value": "={{ $now }}"},
              {"name": "prompt", "value": "={{ $json[\"raw_prompt\"] }}"},
              {"name": "enhanced_prompt", "value": "={{ $node[\"Ollama Prompt Enhance\"].json[\"response\"] || \"\" }}"},
              {"name": "status", "value": "={{ $node[\"Manual Approval\"].json[\"approved\"] ? \"approved\" : \"rejected\" }}"},
              {"name": "posted", "value": "={{ $node[\"Post to Twitter\"].statusCode === 200 }}"}
            ]
          }
        }
      },
      "name": "Log Run",
      "type": "n8n-nodes-base.writeBinaryFile",
      "typeVersion": 1,
      "position": [1750, 350]
    }
  ],
  "connections": {
    "Cron Trigger": {
      "main": [
        [
          {
            "node": "Ollama Prompt Enhance",
            "type": "main",
            "index": 0
          }
        ]
      ]
    },
    "Ollama Prompt Enhance": {
      "main": [
        [
          {
            "node": "Call ComfyUI API",
            "type": "main",
            "index": 0
          }
        ]
      ]
    },
    "Call ComfyUI API": {
      "main": [
        [
          {
            "node": "Poll ComfyUI History",
            "type": "main",
            "index": 0
          }
        ]
      ]
    },
    "Poll ComfyUI History": {
      "main": [
        [
          {
            "node": "Fetch Image",
            "type": "main",
            "index": 0
          }
        ]
      ]
    },
    "Fetch Image": {
      "main": [
        [
          {
            "node": "Manual Approval",
            "type": "main",
            "index": 0
          }
        ]
      ]
    },
    "Manual Approval": {
      "main": [
        [
          {
            "node": "Post to Twitter",
            "type": "main",
            "index": 0
          },
          {
            "node": "Log Run",
            "type": "main",
            "index": 0
          }
        ]
      ]
    }
  },
  "active": false,
  "settings": {},
  "id": "1"
}
```

- [ ] **Step 2: Validate JSON syntax**

```bash
python3 -m json.tool n8n-workflows/marketing-image-generation.json > /dev/null && echo "JSON valid"
```

- [ ] **Step 3: Commit**

```bash
git add n8n-workflows/marketing-image-generation.json
git commit -m "feat: add n8n workflow for hybrid marketing image generation"
```

### Task 3: Create ComfyUI workflow JSON for txt2img → img2img → upscale → bg‑remove

**Files:**
- Create: `comfyui-workflows/marketing-pipeline.json`

**Interfaces:**
- Consumes: Prompt string from n8n (via API)
- Produces: Final image written to storage/output/
- Uses: UltimateSDUpscale, Background Removal (e.g., RMBG) nodes (assumed installed via Impact‑Pack/ControlNet‑aux etc.)

- [ ] **Step 1: Draft ComfyUI workflow**

```json
{
  "last_node_id": 10,
  "last_link_id": 0,
  "nodes": [
    {
      "type": "CheckpointLoaderSimple",
      "id": 1,
      "pos": [100, 100],
      "inputs": {
        "ckpt_name": "juggernaut_xl.safetensors"
      }
    },
    {
      "type": "CLIPTextEncode",
      "id": 2,
      "pos": [300, 100],
      "inputs": {
        "text": "",
        "clip": [1, 1]
      }
    },
    {
      "type": "EmptyLatentImage",
      "id": 3,
      "pos": [300, 200],
      "inputs": {
        "width": 1024,
        "height": 1024,
        "batch_size": 1
      }
    },
    {
      "type": "KSampler",
      "id": 4,
      "pos": [500, 150],
      "inputs": {
        "model": [1, 0],
        "positive": [2, 0],
        "negative": [2, 0],
        "latent_image": [3, 0],
        "seed": 0,
        "steps": 20,
        "cfg": 7,
        "sampler_name": "euler",
        "scheduler": "normal",
        "denoise": 1
      }
    },
    {
      "type": "VAEDecode",
      "id": 5,
      "pos": [700, 150],
      "inputs": {
        "samples": [4, 0],
        "vae": [1, 2]
      }
    },
    {
      "type": "UltimateSDUpscale",
      "id": 6,
      "pos": [900, 100],
      "inputs": {
        "image": [5, 0],
        "upscale_method": "SwimIR",
        "scale": 2,
        "seed": 0,
        "steps": 15,
        "cfg": 7,
        "sampler_name": "euler",
        "scheduler": "normal",
        "denoise": 0.5
      }
    },
    {
      "type": "RemoveBackground",
      "id": 7,
      "pos": [1100, 100],
      "inputs": {
        "image": [6, 0]
      }
    },
    {
      "type": "SaveImage",
      "id": 8,
      "pos": [1300, 100],
      "inputs": {
        "filename_prefix": "marketing_",
        "image": [7, 0]
      }
    }
  ],
  "links": [
    [0, 1, 0, "ckpt_name", 1, 0],
    [0, 2, 0, "text", 2, 0],
    [0, 2, 1, "clip", 1, 1],
    [0, 3, 0, "width", 3, 0],
    [0, 3, 1, "height", 3, 1],
    [0, 3, 2, "batch_size", 3, 2],
    [0, 4, 0, "model", 1, 0],
    [0, 4, 1, "positive", 2, 0],
    [0, 4, 2, "negative", 2, 0],
    [0, 4, 3, "latent_image", 3, 0],
    [0, 5, 0, "samples", 4, 0],
    [0, 5, 1, "vae", 1, 2],
    [0, 6, 0, "image", 5, 0],
    [0, 7, 0, "image", 6, 0],
    [0, 8, 0, "filename_prefix", 8, 0],
    [0, 8, 1, "image", 7, 0]
  ],
  "groups": [],
  "config": {
    "progress_bar": false,
    "progress_bar_upper_range": 0,
    "progress_bar_lower_range": 0
  },
  "version": 0.4
}
```

- [ ] **Step 2: Replace placeholder prompt with variable marker** – we will have n8n inject the prompt into node 2’s "text" field. For simplicity, we can keep as empty and rely on n8n to modify the JSON before posting, or we can use a "String Literal" node that reads from an environment variable. For this plan, we assume n8n will replace the `"text": ""` with the actual prompt.

- [ ] **Step 3: Validate JSON**

```bash
python3 -m json.tool comfyui-workflows/marketing-pipeline.json > /dev/null && echo "JSON valid"
```

- [ ] **Step 4: Commit**

```bash
git add comfyui-workflows/marketing-pipeline.json
git commit -m "feat: add ComfyUI workflow for txt2img→img2img→upscale→bg‑remove"
```

### Task 4: Update docker‑compose to expose necessary ports and ensure volume mounts

We already have the docker‑compose.yml with correct ports and mounts. Just verify and maybe add environment variables for n8n credentials.

**Files:**
- Modify: `docker-compose.yml`

**Interfaces:**
- Consumes: None
- Produces: Updated compose with any needed env vars (e.g., for n8n basic auth, social media API keys)

- [ ] **Step 1: Verify current docker‑compose.yml matches required services**

```bash
grep -E 'image:|ports:|volumes:|environment:' docker-compose.yml
```

- [ ] **Step 2: Add environment variables for n8n (if not present) and for social media API keys (placeholders)**

```yaml
    environment:
      - N8N_BASIC_AUTH_ACTIVE=true
      - N8N_BASIC_AUTH_USER=admin
      - N8N_BASIC_AUTH_PASSWORD=password
      - TWITTER_BEARER_TOKEN=${TWITTER_BEARER_TOKEN}
      - TWITTER_API_URL=https://api.twitter.com/2/tweets
```

- [ ] **Step 3: Ensure ComfyUI service has GPU runtime (already present)**

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml
git commit -m "feat: add env vars for n8n auth and Twitter API placeholders"
```

### Task 5: Create a helper script to inject prompt into ComfyUI workflow before API call (optional)

If we want to avoid modifying the workflow JSON each time, we can create a small Python script that n8n calls to generate the final JSON.

**Files:**
- Create: `scripts/inject_prompt.py`

**Interfaces:**
- Consumes: prompt string, path to base workflow JSON
- Produces: JSON with prompt filled in

- [ ] **Step 1: Write script**

```python
#!/usr/bin/env python3
import json
import sys

def main():
    if len(sys.argv) != 4:
        print("Usage: inject_prompt.py <input_workflow.json> <prompt> <output_workflow.json>")
        sys.exit(1)
    input_path, prompt, output_path = sys.argv[1], sys.argv[2], sys.argv[3]
    with open(input_path, 'r') as f:
        wf = json.load(f)
    # Find CLIPTextEncode node (id 2) and set its text
    for node in wf.get('nodes', []):
        if node.get('type') == 'CLIPTextEncode' and node.get('id') == 2:
            node['inputs']['text'] = prompt
            break
    with open(output_path, 'w') as f:
        json.dump(wf, f, indent=2)

if __name__ == '__main__':
    main()
```

- [ ] **Step 2: Make executable**

```bash
chmod +x scripts/inject_prompt.py
```

- [ ] **Step 3: Commit**

```bash
git add scripts/inject_prompt.py
git commit -m "feat: add helper script to inject prompt into ComfyUI workflow"
```

### Task 6: Test the end‑to‑end flow manually (once)

**Files:**
- None (use existing containers)

**Interfaces:**
- Consumes: Docker services running
- Produces: Verified generation, approval, and logging

- [ ] **Step 1: Start stack (if not already)**

```bash
docker compose up -d
```

- [ ] **Step 2: Trigger n8n workflow manually (via n8n UI) or via curl**

```bash
curl -X POST http://localhost:5678/webhook-test/marketing-trigger \
  -H "Content-Type: application/json" \
  -d '{"raw_prompt": "A modern eco‑friendly product shot on a white background, vibrant colors, studio lighting"}'
```

- [ ] **Step 3: Monitor logs for each service**

```bash
docker compose logs -f comfyui
docker compose logs -f n8n
```

- [ ] **Step 4: Verify image appears in storage/output/**

```bash
ls -la storage/output/
```

- [ ] **Step 5: Check that metadata log was appended**

```bash
cat storage/metadata/run_log.csv
```

- [ ] **Step 6: Commit test notes (optional)**

```bash
git commit --allow-empty -m "test: manual end‑to‑end verification completed"
```

### Task 7: Set up Metabase dashboard (basic)

**Files:**
- None (use Metabase UI)

**Interfaces:**
- Consumes: CSV log file at storage/metadata/run_log.csv
- Produces: Dashboard showing runs over time, success/failure rates

- [ ] **Step 1: Access Metabase at http://localhost:3000** and complete setup (if first time).

- [ ] **Step 2: Add a new database** → "File" → point to the CSV file (or better, use a small SQLite script to import; for simplicity, we can use Metabase’s "CSV" connector).

- [ ] **Step 3: Create a simple dashboard** with:
  * Row count over time
  * Success rate (approved & posted)
  * Average latency (if we add timestamps)

- [ ] **Step 4: Document the steps in a README** (optional)

```bash
mkdir -p docs/metabase
echo "# Metabase Setup\n\n1. Connect to CSV at storage/metadata/run_log.csv\n2. Create dashboard with run counts and success rate." > docs/metabase/README.md
```

- [ ] **Step 5: Commit**

```bash
git add docs/metabase/README.md
git commit -m "feat: add basic Metabase setup instructions"
```

### Task 8: Final review and cleanup

**Files:**
- None

**Interfaces:**
- Ensures all pieces are present and documented

- [ ] **Step 1: Verify all required files exist**

```bash
ls -la n8n-workflows/ comfyui-workflows/ scripts/ docs/superpowers/specs/ docs/superpowers/plans/
```

- [ ] **Step 2: Run a quick sanity check that containers start**

```bash
docker compose up -d && sleep 10 && docker compose ps
```

- [ ] **Step 3: Shutdown stack (leave as is for user)**

```bash
docker compose down
```

- [ ] **Step 4: Commit final state**

```bash
git add .
git commit -m "chore: final verification of marketing AI image generation stack"
```

---
**Plan complete and saved to** `docs/superpowers/plans/2026-08-20-marketing-ai-image-generation-stack-plan.md`. **Two execution options:**

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**