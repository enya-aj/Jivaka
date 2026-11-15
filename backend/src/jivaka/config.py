from functools import lru_cache
from typing import Literal

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

    # Ingestion speed (see "Large documents" in docs/user-manual.md).
    # Most LLM extractions running at once, across all ingests in this
    # backend. 2 suits a single Ollama host: with OLLAMA_NUM_PARALLEL >= 2
    # both run in parallel, and with 1 the second waits in Ollama's queue so
    # the model never idles while results are written to the graph.
    ingest_max_concurrency: int = 2
    # Chunk sizes in characters. Bigger chunks mean fewer LLM calls per
    # document. Changing them means an interrupted ingest of a file can't
    # reuse its finished chunks and starts that file over.
    textbook_chunk_max_chars: int = 1500
    patient_record_chunk_max_chars: int = 1200
    # Extra attempts for a chunk whose extraction failed (timeout, Ollama
    # error, invalid JSON) before it is recorded with no entities.
    ingest_extraction_retries: int = 1

    # Which LLM does entity extraction during ingestion: "ollama" (local,
    # the default) or "openrouter" (cloud API, needs OPENROUTER_API_KEY and
    # OPENROUTER_MODEL). Chat always uses Ollama.
    ingest_llm_provider: Literal["ollama", "openrouter"] = "ollama"
    openrouter_api_key: str = ""
    openrouter_model: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_request_timeout_seconds: float = 120.0
    # Extractions sent to OpenRouter at once. Used instead of
    # INGEST_MAX_CONCURRENCY when OpenRouter does the extraction.
    openrouter_max_concurrency: int = 16
    # Ask OpenRouter to route only to providers that don't store or train on
    # prompts. Leaves fewer providers to choose from for some models.
    openrouter_deny_data_collection: bool = True
    # Patient records stay on Ollama even when INGEST_LLM_PROVIDER is
    # openrouter, unless this is turned on. They are de-identified before
    # extraction, but de-identification can miss things, so sending them to
    # a cloud API is an explicit choice.
    openrouter_for_patient_records: bool = False

    # Chat: how many chunks are given to the model as sources per answer.
    retrieval_top_k: int = 6

    # Sign-in (jivaka.auth). With auth on, every page and API call needs a
    # signed-in, invited user. Only turn it off for local development and
    # tests: everyone is then treated as an admin.
    auth_enabled: bool = True
    # OAuth client from Google Cloud Console (APIs & Services > Credentials,
    # type "Web application"). Its authorized redirect URI must be
    # PUBLIC_BASE_URL + "/auth/callback".
    google_client_id: str = ""
    google_client_secret: str = ""
    # The address people open Jivaka at, e.g. https://187-127-210-88.sslip.io
    public_base_url: str = "http://localhost:8000"
    # Comma-separated Google addresses that are always admins. They can sign
    # in without an invite and add everyone else on the /admin page.
    admin_emails: str = ""
    # Users, sign-in sessions and chat history live in this SQLite file.
    app_db_path: str = "./data/app/jivaka.db"
    session_days: int = 14
    # Session cookies only travel over HTTPS. Turn off for plain-http local
    # development, or the browser drops the cookie and sign-in loops.
    cookie_secure: bool = True

    def admin_email_set(self) -> set[str]:
        return {e.strip().lower() for e in self.admin_emails.split(",") if e.strip()}


@lru_cache
def get_settings() -> Settings:
    """Lazily constructed so importing this module doesn't require OLLAMA_MODEL
    to be set (e.g. for unit tests that never touch the LLM)."""
    return Settings()
