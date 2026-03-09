from urllib.parse import quote

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # PostgreSQL — set individual fields OR override with a full DATABASE_URL
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "zefix"
    postgres_password: str = "password"
    postgres_db: str = "zefix_analyzer"
    # Set this directly to bypass the individual fields above (optional)
    database_url: str = ""

    @model_validator(mode="after")
    def build_database_url(self) -> "Settings":
        if not self.database_url:
            user = quote(self.postgres_user, safe="")
            password = quote(self.postgres_password, safe="")
            self.database_url = (
                f"postgresql://{user}:{password}"
                f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
            )
        return self

    zefix_api_base_url: str = "https://www.zefix.admin.ch/ZefixREST/api/v1"
    zefix_api_username: str = ""
    zefix_api_password: str = ""

    google_search_enabled: bool = True
    google_api_key: str = ""
    google_cse_id: str = ""
    google_daily_quota: int = 100


settings = Settings()
