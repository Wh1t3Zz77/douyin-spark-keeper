from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    app_secret: str = "development-only-change-before-deploy"
    database_url: str = "sqlite:///./data/spark.db"
    allowed_origins: str = "http://localhost:3000"
    session_cookie_name: str = "spark_session"
    session_secure: bool = False
    session_days: int = 30
    bootstrap_invite: str = ""
    bootstrap_invite_uses: int = 20
    admin_email: str = ""
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_ssl: bool = True
    email_verification_required: bool = True
    debug_return_codes: bool = False

    @property
    def origins(self) -> list[str]:
        return [item.strip().rstrip("/") for item in self.allowed_origins.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
