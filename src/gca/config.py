"""Deployment configuration read from the environment.

Only deployment internals live here. Operational settings (org tokens, mail,
AI, recipients, schedules) are managed exclusively in-app through the
settings store; the environment never configures them.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://gca:change-me@localhost:5432/gca"
    app_secret_key: str = ""
    tz: str = "UTC"

    clone_dir: str = "/data/clones"
    reports_dir: str = "/data/reports"


@lru_cache
def get_settings() -> Settings:
    return Settings()
