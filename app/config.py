import secrets
from urllib.parse import urlparse

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "dev"

    # PostgreSQL — set individual fields OR override with a full DATABASE_URL
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "zefix"
    postgres_password: str = "password"
    postgres_db: str = "zefix_analyzer"
    # Set this to bypass the individual fields above (optional)
    database_url: str = ""

    zefix_api_base_url: str = "https://www.zefix.admin.ch/ZefixPublicREST/api/v1"
    zefix_api_username: str = ""
    zefix_api_password: str = ""

    google_search_enabled: bool = True
    serper_api_key: str = ""
    google_daily_quota: int = 100

    # Global URL exclusion: comma-separated keywords (case-insensitive) that, if present
    # anywhere in a candidate URL, force it to be excluded from scoring.
    # This is a simple substring match list (NOT a regex).
    google_url_exclude_keywords: str = ""

    anthropic_api_key: str = ""

    # Background worker
    # Set DISABLE_JOB_WORKER=true to prevent starting the in-process job worker thread.
    # Useful when running the web API in a memory-limited pod and processing jobs elsewhere.
    disable_job_worker: bool = False

    # Redis — used by RQ job queue and rate limiting
    redis_url: str = ""

    # Set USE_RQ=true to enqueue jobs into Redis (RQ) instead of the in-process thread.
    # Requires REDIS_URL and a running RQ worker (app/worker_entrypoint.py).
    use_rq: bool = False

    # SMTP — for email verification and transactional emails
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""          # e.g. "Helvex <noreply@helvex.dicy.ch>"
    app_base_url: str = "https://helvex.dicy.ch"   # used in email links

    # OAuth — Google and LinkedIn sign-in
    # In dev: set APP_BASE_URL=http://localhost:3000 and register
    # http://localhost:3000/api/v1/auth/google/callback (and linkedin) as dev redirect URIs.
    google_client_id: str = ""
    google_client_secret: str = ""
    linkedin_client_id: str = ""
    linkedin_client_secret: str = ""

    # Security — dev gets an ephemeral random key if unset; production must set a strong key.
    secret_key: str = Field(default_factory=lambda: secrets.token_hex(32))

    @model_validator(mode="after")
    def _validate_prod_security(self) -> "Settings":
        env = self.app_env.lower().strip()
        is_prod_like = env in {"prod", "production", "staging"}
        if not is_prod_like:
            return self

        weak_db_passwords = {"", "password", "changeme", "your_password_here"}
        if self.database_url.strip():
            parsed = urlparse(self.database_url)
            scheme = (parsed.scheme or "").lower()
            if not scheme.startswith("postgres"):
                raise ValueError("DATABASE_URL must be a postgres/postgresql URL in production-like environment")

            db_password = (parsed.password or "").strip().lower()
            if db_password in weak_db_passwords:
                raise ValueError("Unsafe DATABASE_URL password for production-like environment")
        else:
            if self.postgres_password.strip().lower() in weak_db_passwords:
                raise ValueError("Unsafe POSTGRES_PASSWORD for production-like environment")

        if len(self.secret_key.strip()) < 32:
            raise ValueError("SECRET_KEY must be set and at least 32 characters in production-like environment")

        if not self.smtp_host.strip() or not self.smtp_from.strip():
            raise ValueError("SMTP_HOST and SMTP_FROM must be set in production-like environment")

        if not self.app_base_url.strip():
            raise ValueError("APP_BASE_URL must be set in production-like environment")

        return self


settings = Settings()
