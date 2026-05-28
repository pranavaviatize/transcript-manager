from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    database_url: str = "sqlite:///data/db.sqlite"
    upload_dir: str = "data/transcripts"
    openrouter_api_key: str = ""
    ai_model: str = "google/gemini-2.0-flash-001"
    app_host: str = "0.0.0.0"
    app_port: int = 8000

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> Settings:
    return Settings()
