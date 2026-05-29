from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    database_url: str = "sqlite:///data/db.sqlite"
    upload_dir: str = "data/transcripts"
    openrouter_api_key: str = ""
    ai_model: str = "google/gemini-2.5-pro"

    # --- Chat / RAG ---
    embedding_model: str = "openai/text-embedding-3-small"
    embedding_dim: int = 1536
    chat_model: str = ""  # generation model for chat; falls back to ai_model when blank
    rerank_enabled: bool = True
    rerank_model: str = "google/gemini-2.5-flash"
    retrieval_candidates: int = 40  # fused shortlist size before rerank
    retrieval_top_k: int = 10  # chunks sent to the LLM

    app_host: str = "0.0.0.0"
    app_port: int = 8000
    auth_key: str = ""
    session_secret: str = ""

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> Settings:
    return Settings()
