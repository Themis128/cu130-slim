# Automated Infographic Creation & LinkedIn Posting Workflow

## Overview
This workflow automates the process of creating a professional 7-slide infographic about any topic and posting it to LinkedIn using AI-generated content and images.

## Workflow Template ID
`4ba2e031-8ae2-4c5d-9397-75ac595370ac` (created in social-automation backend)

## How It Works

### Step-by-Step Process:
1. **Set Topic**: Define the topic for your infographic (e.g., "cloudless.gr services")
2. **Generate Image Prompt**: Creates a detailed prompt for AI image generation based on the topic
3. **Generate Infographic Image**: Uses the prompt to create a professional 7-slide infographic (1080x1350px)
4. **Generate LinkedIn Post Copy**: Creates engaging post copy to accompany the infographic
5. **Get LinkedIn Account**: Retrieves your authenticated LinkedIn account information
6. **Post to LinkedIn**: Queues the infographic image with post copy for immediate publishing
7. **End**: Workflow completion

### Technical Details:
- **Image Dimensions**: 1080x1350px (4:5 ratio - optimal for LinkedIn)
- **AI Model**: Stable Diffusion XL Base 1.0 (configurable)
- **Posting**: Uses the publishing queue for immediate or scheduled posting
- **Authentication**: Uses existing authenticated sessions (no additional login required)

## Usage Instructions

### Method 1: Via API (Programmatic)
```bash
# Execute the workflow with a specific topic
curl -X POST "http://localhost:8083/api/v1/workflows/4ba2e031-8ae2-4c5d-9397-75ac595370ac/execute" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
  -d '{
    "topic": "cloudless.gr: Cloud Computing, Serverless & AI Marketing Solutions"
  }'
```

### Method 2: Via Workflow Generation Endpoint
```bash
# Generate a workflow instance from the template
curl -X POST "http://localhost:8083/api/v1/workflows/generate" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
  -d '{
    "templateId": "4ba2e031-8ae2-4c5d-9397-75ac595370ac",
    "name": "Cloudless.gr Infographic - August 2026",
    "data": {
      "topic": "cloudless.gr services overview"
    }
  }'

# Then execute the generated workflow
# (Response will contain the workflow ID to execute)
```

### Method 3: Via Frontend Interface
If your social-automation frontend has a workflow builder:
1. Navigate to Workflows section
2. Find "Automated Infographic Creation & LinkedIn Posting" template
3. Click "Use Template" or "Create from Template"
4. Set the topic parameter
5. Execute the workflow

## Customization Options

### To Change Image Style:
Modify the `Generate Image Prompt` node's bodyContent:
- Adjust `style` parameter (e.g., "technical diagram", "colorful presentation", "minimalist")
- Change color scheme in description
- Modify icon descriptions

### To Change Posting Behavior:
Modify the `Post to LinkedIn` node:
- Change `scheduled_at` for future posting (ISO timestamp format)
- Add additional media IDs for carousel posts
- Modify targeting for specific LinkedIn audiences

### To Use Different AI Models:
In the `Generate Infographic Image` node:
- Change the `model` in options (check `/ai-providers/catalog` for available models)
- Adjust `steps`, `cfg_scale`, `width`, `height` as needed

## Example Usage for cloudless.gr

To create and post an infographic about cloudless.gr specifically:

```bash
curl -X POST "http://localhost:8083/api/v1/workflows/4ba2e031-8ae2-4c5d-9397-75ac595370ac/execute" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
  -d '{
    "topic": "cloudless.gr: Integrated Cloud Computing, Serverless Architecture, and AI-Powered Marketing Solutions for Business Transformation"
  }'
```

## Monitoring & Management

### Check Workflow Executions:
```bash
GET /api/v1/workflows/{workflowId}/executions
```

### View Generated Media:
```bash
GET /api/v1/media/{mediaId}  # From the workflow output
```

### Check Published Posts:
```bash
GET /api/v1/publishing/history
```

## Requirements
- Authenticated LinkedIn account connected via `/accounts/connect`
- AI image generation provider configured (Stable Diffusion or similar)
- Text generation provider configured
- Publishing service enabled

## Troubleshooting
- **Authentication errors**: Ensure you have a valid access token and LinkedIn account connected
- **Image generation failures**: Check AI provider configuration and prompt content
- **Posting failures**: Verify LinkedIn account permissions and publishing service status
- **Workflow execution issues**: Check workflow logs in the execution details

## Next Steps
1. Connect your LinkedIn account via the frontend or `/accounts/connect` endpoint
2. Verify AI providers are configured and working
3. Test the workflow with a simple topic first
4. Schedule regular content creation for ongoing LinkedIn presence
5. Consider creating additional workflow templates for different content types (carousels, videos, etc.)

---

**Workflow Created**: August 23, 2026  
**Template ID**: 4ba2e031-8ae2-4c5d-9397-75ac595370ac  
**Namespace**: social-automation-api