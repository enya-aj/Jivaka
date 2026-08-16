import os

# Ingestion config requires OLLAMA_MODEL to be set (fail-fast by design - see
# jivaka.config.Settings). Tests that never touch the LLM still import modules
# that reference config lazily, so set a harmless placeholder up front.
os.environ.setdefault("OLLAMA_MODEL", "test-model")
