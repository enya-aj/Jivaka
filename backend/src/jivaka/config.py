from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    falkor_host: str = "localhost"
    falkor_port: int = 6379
    falkor_graph_name: str = "jivaka"

    ollama_host: str = "http://localhost:11434"
    # No default on purpose: extraction must fail fast if no model has been chosen,
    # rather than silently calling a nonexistent Ollama model.
    ollama_model: str

    definition_source: str = "stub"
    upload_dir: str = "./data/uploads"
    ocr_text_density_threshold: float = 0.005


@lru_cache
def get_settings() -> Settings:
    """Lazily constructed so importing this module doesn't require OLLAMA_MODEL
    to be set (e.g. for unit tests that never touch the LLM)."""
    return Settings()
