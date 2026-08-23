from functools import lru_cache
import os
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App
    APP_NAME: str = "Social Automation API"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False
    API_PREFIX: str = "/api/v1"

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres_password@social-postgres:5432/social"

    # JWT
    JWT_SECRET_KEY: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Encryption for OAuth tokens
    ENCRYPTION_KEY: str = "change-me-32-chars-minimum!!"

    # n8n
    N8N_API_URL: str = "http://n8n:5678"
    N8N_API_KEY: str

    # ComfyUI
    COMFYUI_URL: str = "http://comfyui:8000"

    # ChromaDB
    CHROMA_URL: str = "http://chromadb:8000"

    # Ollama
    OLLAMA_URL: str = "http://ollama:11434"
    OLLAMA_DEFAULT_MODEL: str = "llama3"

    # Local NVIDIA NIM for Stable Diffusion 3.5
    LOCAL_NIM_URL: str = "http://host.docker.internal:8000/v1/infer"

    # Redis (for Celery/queue)
    REDIS_URL: str = "redis://redis:6379/0"

    # CORS
    CORS_ORIGINS: list[str] = [
        "http://localhost:8083",
        "http://localhost:3000",
        "http://localhost:3001",
        "http://localhost:8082",
    ]

    # Social OAuth
    LINKEDIN_CLIENT_ID: str = ""
    LINKEDIN_CLIENT_SECRET: str = ""
    LINKEDIN_REDIRECT_URI: str = "http://localhost:8083/settings/accounts/callback/linkedin"

    TWITTER_CLIENT_ID: str = ""
    TWITTER_CLIENT_SECRET: str = ""
    TWITTER_REDIRECT_URI: str = "http://localhost:8083/settings/accounts/callback/twitter"

    INSTAGRAM_CLIENT_ID: str = ""
    INSTAGRAM_CLIENT_SECRET: str = ""
    INSTAGRAM_REDIRECT_URI: str = "http://localhost:8083/settings/accounts/callback/instagram"

    FACEBOOK_CLIENT_ID: str = ""
    FACEBOOK_CLIENT_SECRET: str = ""
    FACEBOOK_REDIRECT_URI: str = "http://localhost:8083/settings/accounts/callback/facebook"

    THREADS_CLIENT_ID: str = ""
    THREADS_CLIENT_SECRET: str = ""
    THREADS_REDIRECT_URI: str = "http://localhost:8083/settings/accounts/callback/threads"


settings = Settings()


@lru_cache
def get_settings() -> Settings:
    return Settings()
