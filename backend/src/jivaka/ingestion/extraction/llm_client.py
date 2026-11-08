import json
import time
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
        """Runs a JSON-format /api/generate and returns the parsed object.

        The reply is streamed, so the request timeout applies to the gap
        between pieces of output rather than to the whole generation: a long
        but steadily progressing extraction (or one that waited in Ollama's
        queue behind another request) is not cut off."""
        for attempt in range(2):
            with self._client.stream("POST", "/api/generate", json=self._generate_body(prompt)) as response:
                if response.status_code == 404 and attempt == 0:
                    response.read()
                    self._pull_model()
                    continue
                if response.is_error:
                    response.read()
                response.raise_for_status()
                pieces: list[str] = []
                for line in response.iter_lines():
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    if data.get("error"):
                        raise RuntimeError(f"Ollama error: {data['error']}")
                    pieces.append(data.get("response", ""))
                    if data.get("done"):
                        break
                return json.loads("".join(pieces))
        raise RuntimeError("unreachable")

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

    def _generate_body(self, prompt: str) -> dict:
        return {"model": self.model, "prompt": prompt, "format": "json", "stream": True}

    def _pull_model(self) -> None:
        response = self._client.post(
            "/api/pull",
            json={"name": self.model, "stream": False},
            timeout=None,
        )
        response.raise_for_status()


class OpenRouterClient:
    """Calls an OpenRouter chat model (OpenAI-compatible API) for JSON
    extraction. Only generate_json is offered: chat stays on Ollama.

    Rate limits (429) and provider hiccups (5xx) are retried here with
    backoff, honouring Retry-After, since a large ingest makes tens of
    thousands of calls; other errors are raised for the extractor's own
    retry and failed-chunk handling."""

    _RETRYABLE = {408, 429, 500, 502, 503, 504}

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        max_attempts: int = 6,
    ):
        settings = get_settings()
        self.api_key = api_key or settings.openrouter_api_key
        self.model = model or settings.openrouter_model
        if not self.api_key:
            raise ValueError("INGEST_LLM_PROVIDER=openrouter needs OPENROUTER_API_KEY")
        if not self.model:
            raise ValueError("INGEST_LLM_PROVIDER=openrouter needs OPENROUTER_MODEL")
        self.deny_data_collection = settings.openrouter_deny_data_collection
        self.max_attempts = max_attempts
        self._sleep = time.sleep
        self._client = httpx.Client(
            base_url=base_url or settings.openrouter_base_url,
            timeout=settings.openrouter_request_timeout_seconds,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "X-Title": "Jivaka",
            },
        )

    def generate_json(self, prompt: str) -> dict:
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
            "temperature": 0,
            # Only route to providers that actually honour response_format.
            "provider": {"require_parameters": True},
        }
        if self.deny_data_collection:
            body["provider"]["data_collection"] = "deny"

        for attempt in range(self.max_attempts):
            try:
                response = self._client.post("/chat/completions", json=body)
            except httpx.TransportError:
                if attempt == self.max_attempts - 1:
                    raise
                self._sleep(min(60, 2**attempt))
                continue
            if response.status_code in self._RETRYABLE and attempt < self.max_attempts - 1:
                self._sleep(_retry_after(response) or min(60, 2**attempt))
                continue
            if response.is_error:
                raise RuntimeError(f"OpenRouter error {response.status_code}: {response.text[:500]}")
            data = response.json()
            if data.get("error"):
                raise RuntimeError(f"OpenRouter error: {data['error']}")
            content = data["choices"][0]["message"].get("content") or ""
            return _parse_json_reply(content)
        raise RuntimeError("unreachable")


def _retry_after(response: httpx.Response) -> Optional[float]:
    try:
        return min(120.0, float(response.headers["retry-after"]))
    except (KeyError, ValueError):
        return None


def _parse_json_reply(content: str) -> dict:
    """Parses a model's JSON reply, tolerating a ```json fence or a little
    text around the object, which some models add despite JSON mode."""
    text = content.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise
        return json.loads(text[start : end + 1])


def build_extraction_client(doc_type: Optional[str] = None):
    """The client ingestion should use for this document type, following
    INGEST_LLM_PROVIDER. Patient records stay on Ollama unless
    OPENROUTER_FOR_PATIENT_RECORDS is on."""
    if _uses_openrouter(doc_type):
        return OpenRouterClient()
    return OllamaClient()


def extraction_llm(doc_type: Optional[str] = None) -> dict:
    """The provider and model build_extraction_client would pick for this
    document type, read from settings without building a client."""
    settings = get_settings()
    if _uses_openrouter(doc_type):
        return {"provider": "openrouter", "model": settings.openrouter_model}
    return {"provider": "ollama", "model": settings.ollama_model}


def _uses_openrouter(doc_type: Optional[str]) -> bool:
    settings = get_settings()
    return settings.ingest_llm_provider == "openrouter" and (
        doc_type != "patient_record" or settings.openrouter_for_patient_records
    )
