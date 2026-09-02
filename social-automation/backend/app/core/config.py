from functools import lru_cache

from pydantic import field_validator
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
    # Set to "test" or "development" to disable the rate-limiter per-request key.
    APP_ENV: str = "production"
    API_PREFIX: str = "/api/v1"
    # Display / scheduling timezone (Greece). DB timestamps remain UTC.
    APP_TIMEZONE: str = "Europe/Athens"

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres_password@social-postgres:5432/social"

    # JWT
    JWT_SECRET_KEY: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"

    @field_validator("JWT_SECRET_KEY")
    @classmethod
    def validate_jwt_secret(cls, v: str) -> str:
        if len(v) < 32 or v == "change-me-in-production":
            raise ValueError(
                "JWT_SECRET_KEY must be at least 32 characters and cannot be the default value. "
                "Set it in .env: JWT_SECRET_KEY=$(python3 -c \"import secrets; print(secrets.token_hex(32))\")"
            )
        return v
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

    # Ollama — default to the GPU-optimized tag (all layers on VRAM, q8_0 KV cache)
    OLLAMA_URL: str = "http://ollama:11434"
    OLLAMA_DEFAULT_MODEL: str = "llama3.1:8b-gpu"
    OLLAMA_EMBEDDING_MODEL: str = "nomic-embed-text"

    # LanguageTool self-hosted spell/grammar checker
    LANGUAGETOOL_URL: str = "http://languagetool:8010"

    # Local NVIDIA NIM for Stable Diffusion 3.5
    LOCAL_NIM_URL: str = "http://host.docker.internal:8000/v1/infer"

    # Cloud AI Provider API Keys
    GROQ_API_KEY: str = ""
    TOGETHER_API_KEY: str = ""
    PIXAZO_API_KEY: str = ""
    NVIDIA_API_KEY: str = ""
    HUGGINGFACE_API_KEY: str = ""
    GEMINI_API_KEY: str = ""
    OPENROUTER_API_KEY: str = ""
    SAMBANOVA_API_KEY: str = ""
    MISTRAL_API_KEY: str = ""
    COHERE_API_KEY: str = ""
    FAL_KEY: str = ""
    OPENAI_API_KEY: str = ""
    CLOUDFLARE_API_TOKEN: str = ""
    CLOUDFLARE_AI_API_TOKEN: str = ""
    CLOUDFLARE_ACCOUNT_ID: str = ""

    # Cloudflare D1 (primary SQL databases)
    D1_SOCIAL_AUTOMATION_ID: str = ""
    D1_N8N_ID: str = ""
    D1_METABASE_ID: str = ""

    # Cloudflare KV (cache + queue)
    KV_CACHE_NAMESPACE: str = ""
    KV_QUEUE_NAMESPACE: str = ""

    # Cloudflare Vectorize (embeddings)
    VECTORIZE_INDEX_NAME: str = "social-embeddings"

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
    LINKEDIN_USERNAME: str = ""
    LINKEDIN_PASSWORD: str = ""

    TWITTER_CLIENT_ID: str = ""
    TWITTER_CLIENT_SECRET: str = ""
    TWITTER_REDIRECT_URI: str = "http://localhost:8083/api/v1/auth/oauth/twitter/callback"

    INSTAGRAM_CLIENT_ID: str = ""
    INSTAGRAM_CLIENT_SECRET: str = ""
    INSTAGRAM_REDIRECT_URI: str = "http://localhost:8083/api/v1/auth/oauth/instagram/callback"

    # Instagram Business Login (Instagram API with Instagram Login, graph.instagram.com)
    INSTAGRAM2_CLIENT_ID: str = ""
    INSTAGRAM2_CLIENT_SECRET: str = ""
    INSTAGRAM2_REDIRECT_URI: str = "http://localhost:8083/api/v1/auth/oauth/instagram2/callback"

    # Instagram Private API (aiograpi-rest Docker sidecar)
    # Used for profile writes (bio, picture, name, website, phone) that the
    # official Graph API does not support.  The sidecar runs on port 8000
    # inside the compose network, exposed as 8010 on the host.
    INSTAGRAM_PRIVATE_API_URL: str = "http://instagram-private-api:8000"
    INSTAGRAM_USERNAME: str = ""
    INSTAGRAM_PASSWORD: str = ""

    # Residential/mobile proxy for Instagram private API — datacenter IPs
    # get challenge_required from Instagram's anti-abuse system.
    # Format: http://user:pass@host:port  or  socks5://user:pass@host:port
    # Default: Cloudflare WARP proxy (free, non-datacenter IP)
    INSTAGRAM_PROXY: str = "socks5://warp-proxy:1080"

    # Browser bridge (browser-novnc container) — used as fallback for
    # Instagram profile operations when the aiograpi-rest sidecar fails
    # (challenge_required, login_required, fingerprint mismatch).
    BROWSER_BRIDGE_URL: str = "http://browser-novnc:9223"

    FACEBOOK_CLIENT_ID: str = ""
    FACEBOOK_CLIENT_SECRET: str = ""
    FACEBOOK_REDIRECT_URI: str = "http://localhost:8083/api/v1/auth/oauth/facebook/callback"
    FACEBOOK_USERNAME: str = ""
    FACEBOOK_PASSWORD: str = ""

    THREADS_CLIENT_ID: str = ""
    THREADS_CLIENT_SECRET: str = ""
    THREADS_REDIRECT_URI: str = "http://localhost:8083/api/v1/auth/oauth/threads/callback"

    TIKTOK_CLIENT_KEY: str = ""
    TIKTOK_CLIENT_SECRET: str = ""
    TIKTOK_REDIRECT_URI: str = "http://localhost:8083/api/v1/auth/oauth/tiktok/callback"

    # TikTok Private API (tiktokflow / tiktok-private-api)
    # Used for profile writes (nickname, signature/bio, avatar, unique ID)
    # that the official Display API does not support.  The signing server
    # handles X-Argus / X-Ladon / X-Gorgon request signing.
    TIKTOK_PRIVATE_API_KEY: str = ""

    # Twitter v1.0a app credentials (for media upload via v1.1 API)
    TWITTER_API_KEY: str = ""
    TWITTER_API_SECRET: str = ""
    TWITTER_ACCESS_TOKEN: str = ""
    TWITTER_ACCESS_TOKEN_SECRET: str = ""

    # Media serving — set to a publicly reachable base URL so Instagram
    # can fetch images. E.g. https://yourdomain.com or an ngrok URL.
    # When empty, Instagram posts without images fall back gracefully.
    MEDIA_PUBLIC_BASE_URL: str = ""

    # Cloudflare R2 (free object storage / free egress)
    # R2 is used via the Cloudflare REST API (PUT object, max 300MB) or the
    # S3-compatible API. R2_PUBLIC_URL must end with a trailing slash.
    R2_BUCKET_NAME: str = ""
    R2_PUBLIC_URL: str = ""
    R2_API_BASE: str = "https://api.cloudflare.com/client/v4"
    # S3-compatible credentials for presigned upload/download URLs.
    # If not set, presigned URLs are unavailable and the REST API is used
    # for server-side uploads.
    R2_ACCESS_KEY_ID: str = ""
    R2_SECRET_ACCESS_KEY: str = ""
    R2_S3_ENDPOINT: str = ""

    # Webhook fired after every successful publish. Payload is a JSON object:
    # {event, post_id, platform, account_id, platform_url, published_at,
    #  workflow_run_id, workflow_id}
    PUBLISH_SUCCESS_WEBHOOK_URL: str = ""

    # MinIO (internal object storage)
    MINIO_ENDPOINT: str = "minio:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin"
    MINIO_BUCKET: str = "social-media"
    MINIO_SECURE: bool = False

    # Admin user auto-seeding
    SOCIAL_ADMIN_EMAIL: str = ""
    SOCIAL_ADMIN_PASSWORD: str = ""
    SOCIAL_ADMIN_NAME: str = "Admin User"

    # Slack daily digest (#socialauto)
    # Prefer Incoming Webhook URL; alternatively bot/user token + channel id.
    SLACK_WEBHOOK_URL: str = ""
    SLACK_BOT_TOKEN: str = ""
    SLACK_ACCESS_TOKEN: str = ""  # Slack CLI / OAuth access (xoxe.xoxp- / xoxb-)
    SLACK_REFRESH_TOKEN: str = ""
    SLACK_CHANNEL_ID: str = "C0BT263L17U"  # #socialauto
    SLACK_DIGEST_HOUR: int = 9  # Europe/Athens via Celery timezone

    # Free email digests → tbaltzakis@cloudless.gr mailbox (dedicated client / dovecot)
    # EMAIL_PROVIDER=smtp|local|cloudflare
    # smtp = Resend (same free relay as omv-ha mail); cloudflare = paid, unused
    EMAIL_PROVIDER: str = "smtp"
    SMTP_HOST: str = "smtp.resend.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = "resend"
    SMTP_PASSWORD: str = ""
    SMTP_USE_TLS: bool = True
    SMTP_FROM: str = "noreply@cloudless.gr"
    DIGEST_EMAIL_TO: str = "tbaltzakis@cloudless.gr"
    DIGEST_EMAIL_ISSUES_ONLY: bool = False
    CLOUDFLARE_EMAIL_API_TOKEN: str = ""  # unused unless EMAIL_PROVIDER=cloudflare (paid)


@lru_cache
def get_settings() -> Settings:
    return Settings()


# Module-level binding for callers that do `from app.core.config import settings`.
# Points to the same lru_cache singleton as get_settings() — not a second instance.
settings = get_settings()
