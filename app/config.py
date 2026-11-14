"""All settings come from environment variables (or a .env file).

Why: the same code must run on a laptop and on AWS. Only the environment changes,
never the code. See docs/01-setup.md for what each setting means.
"""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # extra="ignore": unrelated variables in .env (e.g. for docker compose) don't cause errors.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    provider: Literal["ollama", "bedrock"] = "ollama"
    database_url: str = "postgresql://rag:rag@localhost:5432/trials"

    ollama_base_url: str = "http://localhost:11434"
    llm_model: str = "llama3.1:8b"
    embedding_model: str = "mxbai-embed-large"
    # Must match the vector column size in the database. 1024 works for both the local
    # model and Bedrock Titan v2 — see docs/03-chunking-and-embeddings.md.
    embedding_dim: int = 1024

    top_k: int = 8
    request_timeout_s: float = 120.0

    aws_region: str = "eu-central-1"


@lru_cache
def get_settings() -> Settings:
    """Read settings once and reuse them (reading .env on every request would be wasteful)."""
    return Settings()
