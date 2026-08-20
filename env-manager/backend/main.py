from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict, Optional, List
import os
import secrets
from pathlib import Path
from dotenv import load_dotenv, dotenv_values

app = FastAPI(title="Env Manager API", version="1.0.0")

# CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Security
security = HTTPBasic()
ENV_FILE = Path("/app/.env")
ENV_MANAGER_USER = os.getenv("ENV_MANAGER_USER", "admin")
ENV_MANAGER_PASS = os.getenv("ENV_MANAGER_PASS", "admin")

# Env variable definitions with metadata
ENV_DEFINITIONS = {
    # Social Media APIs
    "TWITTER_API_URL": {"category": "Social Media", "description": "Twitter/X API v2 endpoint", "required": True, "default": "https://api.twitter.com/2/tweets"},
    "TWITTER_BEARER_TOKEN": {"category": "Social Media", "description": "Twitter/X Bearer Token", "required": False, "sensitive": True},
    "TWITTER_API_KEY": {"category": "Social Media", "description": "Twitter/X API Key", "required": False, "sensitive": True},
    "TWITTER_API_SECRET": {"category": "Social Media", "description": "Twitter/X API Secret", "required": False, "sensitive": True},
    "TWITTER_ACCESS_TOKEN": {"category": "Social Media", "description": "Twitter/X Access Token", "required": False, "sensitive": True},
    "TWITTER_ACCESS_TOKEN_SECRET": {"category": "Social Media", "description": "Twitter/X Access Token Secret", "required": False, "sensitive": True},
    "INSTAGRAM_ACCESS_TOKEN": {"category": "Social Media", "description": "Instagram Graph API Access Token", "required": False, "sensitive": True},
    "FACEBOOK_APP_ID": {"category": "Social Media", "description": "Facebook App ID", "required": False, "sensitive": True},
    "FACEBOOK_APP_SECRET": {"category": "Social Media", "description": "Facebook App Secret", "required": False, "sensitive": True},
    "LINKEDIN_ACCESS_TOKEN": {"category": "Social Media", "description": "LinkedIn API Access Token", "required": False, "sensitive": True},
    "LINKEDIN_CLIENT_ID": {"category": "Social Media", "description": "LinkedIn Client ID", "required": False, "sensitive": True},
    "LINKEDIN_CLIENT_SECRET": {"category": "Social Media", "description": "LinkedIn Client Secret", "required": False, "sensitive": True},
    # AI Services
    "REPLICATE_API_TOKEN": {"category": "AI Services", "description": "Replicate API Token", "required": False, "sensitive": True},
    "FAL_API_KEY": {"category": "AI Services", "description": "FAL.ai API Key", "required": False, "sensitive": True},
    # Core Config
    "WEBHOOK_SECRET": {"category": "Core", "description": "Webhook secret for n8n", "required": True, "sensitive": True, "default": "change-me"},
    "N8N_API_KEY": {"category": "Core", "description": "n8n API Key", "required": True, "sensitive": True},
    "N8N_BASIC_AUTH_USER": {"category": "Core", "description": "n8n Basic Auth User", "required": True},
    "N8N_BASIC_AUTH_PASSWORD": {"category": "Core", "description": "n8n Basic Auth Password", "required": True, "sensitive": True},
    "COMFYUI_MODEL": {"category": "Core", "description": "ComfyUI Checkpoint Model", "required": True, "default": "Juggernaut-XL_v9_RunDiffusionPhoto_v2.safetensors"},
    "UPSCALE_MODEL": {"category": "Core", "description": "Upscale Model", "required": True, "default": "4x-UltraSharp.pth"},
    "OLLAMA_MODEL": {"category": "Core", "description": "Ollama Model Name", "required": True, "default": "llama3"},
}

def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)):
    correct_username = secrets.compare_digest(credentials.username, ENV_MANAGER_USER)
    correct_password = secrets.compare_digest(credentials.password, ENV_MANAGER_PASS)
    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username

def load_env() -> Dict[str, str]:
    """Load .env file, return dict"""
    if ENV_FILE.exists():
        return dotenv_values(ENV_FILE)
    return {}

def save_env(env_vars: Dict[str, str]):
    """Save env vars to .env file"""
    lines = []
    for key, value in env_vars.items():
        if value is not None:
            lines.append(f'{key}="{value}"')
    ENV_FILE.write_text("\n".join(lines) + "\n")

def mask_value(value: str, sensitive: bool = False) -> str:
    """Mask sensitive values for display"""
    if not value or not sensitive:
        return value
    if len(value) <= 8:
        return "*" * len(value)
    return value[:4] + "*" * (len(value) - 8) + value[-4:]

class EnvVarResponse(BaseModel):
    key: str
    value: str
    masked_value: str
    category: str
    description: str
    required: bool
    sensitive: bool

class EnvUpdateRequest(BaseModel):
    updates: Dict[str, str]

@app.get("/api/env", response_model=List[EnvVarResponse])
def get_env(username: str = Depends(verify_credentials)):
    """Get all environment variables with metadata"""
    current = load_env()
    result = []
    for key, meta in ENV_DEFINITIONS.items():
        value = current.get(key, meta.get("default", ""))
        result.append(EnvVarResponse(
            key=key,
            value=value,
            masked_value=mask_value(value, meta.get("sensitive", False)),
            category=meta["category"],
            description=meta["description"],
            required=meta["required"],
            sensitive=meta.get("sensitive", False)
        ))
    return result

@app.get("/api/env/template")
def get_template(username: str = Depends(verify_credentials)):
    """Get env template with descriptions"""
    return ENV_DEFINITIONS

@app.post("/api/env")
def update_env(request: EnvUpdateRequest, username: str = Depends(verify_credentials)):
    """Update environment variables"""
    current = load_env()
    for key, value in request.updates.items():
        if key in ENV_DEFINITIONS:
            current[key] = value
    save_env(current)
    return {"status": "saved", "updated": list(request.updates.keys())}

@app.get("/health")
def health():
    return {"status": "ok"}