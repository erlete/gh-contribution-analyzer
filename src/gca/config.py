"""Deployment configuration read from the environment.

Only deployment internals live here. Operational settings (org tokens, mail, AI,
recipients, schedules) are managed in-app through the settings store; the MAIL_*,
SMTP_* and AI_* fields below merely seed initial defaults on first boot.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://gca:change-me@localhost:5432/gca"
    app_secret_key: str = ""
    app_base_url: str = "http://localhost"
    tz: str = "UTC"

    clone_dir: str = "/data/clones"
    reports_dir: str = "/data/reports"

    mail_azure_client_id: str = ""
    mail_azure_client_secret: str = ""
    mail_azure_tenant_id: str = ""
    mail_sender_address: str = ""

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = True
    smtp_sender_address: str = ""

    ai_service_url: str = ""
    ai_service_key: str = ""
    ai_service_model: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
