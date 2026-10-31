import json
from typing import Iterator, Optional

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
        self._client = httpx.Client(base_url=self.host, timeout=settings.ollama_request_timeout_seconds)

    def generate_json(self, prompt: str) -> dict:
        response = self._post_generate(prompt)
        if response.status_code == 404:
            self._pull_model()
            response = self._post_generate(prompt)
        response.raise_for_status()
        data = response.json()
        return json.loads(data["response"])

    def chat_stream(self, messages: list[dict]) -> Iterator[str]:
        """Streams an /api/chat answer, yielding text pieces as Ollama
        produces them. Uses the same model as extraction."""
        for attempt in range(2):
            with self._client.stream(
                "POST",
                "/api/chat",
                json={"model": self.model, "messages": messages, "stream": True},
            ) as response:
                if response.status_code == 404 and attempt == 0:
                    response.read()
                    self._pull_model()
                    continue
                if response.is_error:
                    response.read()
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    if data.get("error"):
                        raise RuntimeError(f"Ollama error: {data['error']}")
                    piece = data.get("message", {}).get("content", "")
                    if piece:
                        yield piece
                    if data.get("done"):
                        return
                return

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
