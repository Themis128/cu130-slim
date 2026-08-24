from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# Compute env file path: /app/.env (mounted from host)
ENV_FILE_PATH = "/app/.env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE_PATH,
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
    N8N_API_KEY: str = ""
    N8N_USER: str = ""
    N8N_PASSWORD: str = ""

    # ComfyUI
    COMFYUI_URL: str = "http://comfyui:8000"

    # ChromaDB
    CHROMA_URL: str = "http://chromadb:8000"

    # Ollama
    OLLAMA_URL: str = "http://ollama:11434"
    OLLAMA_DEFAULT_MODEL: str = "llama3.1:8b"
    OLLAMA_EMBEDDING_MODEL: str = "nomic-embed-text"

    # Local NVIDIA NIM for Stable Diffusion 3.5
    LOCAL_NIM_URL: str = "http://host.docker.internal:8000/v1/infer"

    # Cloud AI Provider API Keys
    GROQ_API_KEY: str = ""
    TOGETHER_API_KEY: str = ""
    NVIDIA_API_KEY: str = ""
    HUGGINGFACE_API_KEY: str = ""
    FAL_KEY: str = ""
    OPENAI_API_KEY: str = ""
    CLOUDFLARE_API_TOKEN: str = ""
    CLOUDFLARE_ACCOUNT_ID: str = ""

    # Redis (for Celery/queue)
    REDIS_URL: str = "redis://redis:6379/0"

    # CORS - stored as comma-separated string in env, parsed to list
    CORS_ORIGINS_STR: str = "http://localhost:8083,http://localhost:3000,http://localhost:3001,http://localhost:8082"

    @property
    def CORS_ORIGINS(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS_STR.split(",")]

    # Social OAuth
    LINKEDIN_CLIENT_ID: str = ""
    LINKEDIN_CLIENT_SECRET: str = ""
    LINKEDIN_REDIRECT_URI: str = "http://localhost:8083/api/v1/auth/oauth/linkedin/callback"

    TWITTER_CLIENT_ID: str = ""
    TWITTER_CLIENT_SECRET: str = ""
    TWITTER_REDIRECT_URI: str = "http://localhost:8083/api/v1/auth/oauth/twitter/callback"

    INSTAGRAM_CLIENT_ID: str = ""
    INSTAGRAM_CLIENT_SECRET: str = ""
    INSTAGRAM_REDIRECT_URI: str = "http://localhost:8083/api/v1/auth/oauth/instagram/callback"

    FACEBOOK_CLIENT_ID: str = ""
    FACEBOOK_CLIENT_SECRET: str = ""
    FACEBOOK_REDIRECT_URI: str = "http://localhost:8083/api/v1/auth/oauth/facebook/callback"

    THREADS_CLIENT_ID: str = ""
    THREADS_CLIENT_SECRET: str = ""
    THREADS_REDIRECT_URI: str = "http://localhost:8083/api/v1/auth/oauth/threads/callback"

    # Admin user auto-seeding
    SOCIAL_ADMIN_EMAIL: str = ""
    SOCIAL_ADMIN_PASSWORD: str = ""
    SOCIAL_ADMIN_NAME: str = "Admin User"


settings = Settings()


@lru_cache
def get_settings() -> Settings:
    return Settings()
