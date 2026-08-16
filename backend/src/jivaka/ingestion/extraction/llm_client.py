import json
from typing import Optional

import httpx

from jivaka.config import get_settings


class OllamaClient:
    """Thin wrapper around the Ollama HTTP API. Model name comes from
    OLLAMA_MODEL (no default - see config.py) so it's easy to swap models
    without touching code."""

    def __init__(self, host: Optional[str] = None, model: Optional[str] = None):
        settings = get_settings()
        self.host = host or settings.ollama_host
        self.model = model or settings.ollama_model
        self._client = httpx.Client(base_url=self.host, timeout=120.0)

    def generate_json(self, prompt: str) -> dict:
        response = self._post_generate(prompt)
        if response.status_code == 404:
            self._pull_model()
            response = self._post_generate(prompt)
        response.raise_for_status()
        data = response.json()
        return json.loads(data["response"])

    def _post_generate(self, prompt: str) -> httpx.Response:
        return self._client.post(
            "/api/generate",
            json={
                "model": self.model,
                "prompt": prompt,
                "format": "json",
                "stream": False,
            },
        )

    def _pull_model(self) -> None:
        response = self._client.post(
            "/api/pull",
            json={"name": self.model, "stream": False},
            timeout=None,
        )
        response.raise_for_status()
