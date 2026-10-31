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
    # CPU-bound generation on a contended/shared Ollama instance can take
    # several minutes per chunk; a too-short timeout here is swallowed by the
    # per-chunk error handling in llm_extractor.py and silently yields 0
    # entities for that chunk rather than a visible failure.
    ollama_request_timeout_seconds: float = 300.0

    # De-identification of patient records (jivaka.deid). NER needs the
    # spaCy and PyThaiNLP models baked into the Docker image; turning it off
    # leaves only the rule-based recognizers and should be for tests only.
    deid_use_ner: bool = True
    # Extra regexes for hospital-specific ID formats, as a JSON list, e.g.
    # DEID_EXTRA_ID_PATTERNS='["\\b\\d{2}-\\d{6}\\b"]'. Every match becomes [ID].
    deid_extra_id_patterns: list[str] = []

    # Chat: how many chunks are given to the model as sources per answer.
    retrieval_top_k: int = 6


@lru_cache
def get_settings() -> Settings:
    """Lazily constructed so importing this module doesn't require OLLAMA_MODEL
    to be set (e.g. for unit tests that never touch the LLM)."""
    return Settings()
