import json

import httpx
import pytest

from jivaka.config import get_settings
from jivaka.ingestion.extraction.llm_client import (
    OllamaClient,
    OpenRouterClient,
    _parse_json_reply,
    build_extraction_client,
    extraction_llm,
)


def client_with(handler, **kwargs):
    client = OpenRouterClient(api_key="k", model="vendor/model", **kwargs)
    client._client = httpx.Client(base_url="https://or.test/api/v1", transport=httpx.MockTransport(handler))
    client._sleep = lambda s: None
    return client


def reply(content):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def test_generate_json_sends_a_json_mode_request():
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return reply('{"entities": [], "relations": []}')

    assert client_with(handler).generate_json("p") == {"entities": [], "relations": []}
    body = seen["body"]
    assert seen["path"] == "/api/v1/chat/completions"
    assert body["model"] == "vendor/model"
    assert body["messages"] == [{"role": "user", "content": "p"}]
    assert body["response_format"] == {"type": "json_object"}
    assert body["provider"]["require_parameters"] is True
    assert body["provider"]["data_collection"] == "deny"


def test_generate_json_retries_rate_limits_then_succeeds():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(429, headers={"retry-after": "1"}, json={"error": "rate limited"})
        return reply("{}")

    assert client_with(handler).generate_json("p") == {}
    assert len(calls) == 3


def test_generate_json_raises_on_client_error_without_retrying():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    with pytest.raises(RuntimeError, match="401"):
        client_with(handler).generate_json("p")
    assert len(calls) == 1


def test_generate_json_gives_up_after_max_attempts():
    def handler(request):
        return httpx.Response(503, text="down")

    with pytest.raises(RuntimeError, match="503"):
        client_with(handler, max_attempts=3).generate_json("p")


def test_parse_json_reply_tolerates_a_code_fence():
    assert _parse_json_reply('```json\n{"entities": []}\n```') == {"entities": []}


def test_missing_key_fails_fast(monkeypatch):
    monkeypatch.setattr(get_settings(), "openrouter_api_key", "")
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        OpenRouterClient(model="m")


@pytest.mark.parametrize(
    "provider, for_patients, doc_type, expected",
    [
        ("ollama", False, "textbook", OllamaClient),
        ("openrouter", False, "textbook", OpenRouterClient),
        ("openrouter", False, "patient_record", OllamaClient),
        ("openrouter", True, "patient_record", OpenRouterClient),
    ],
)
def test_build_extraction_client_keeps_patient_records_local_by_default(
    monkeypatch, provider, for_patients, doc_type, expected
):
    settings = get_settings()
    monkeypatch.setattr(settings, "ingest_llm_provider", provider)
    monkeypatch.setattr(settings, "openrouter_for_patient_records", for_patients)
    monkeypatch.setattr(settings, "openrouter_api_key", "k")
    monkeypatch.setattr(settings, "openrouter_model", "m")
    assert isinstance(build_extraction_client(doc_type), expected)


def test_extraction_llm_names_the_model_build_extraction_client_would_use(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "ingest_llm_provider", "openrouter")
    monkeypatch.setattr(settings, "openrouter_for_patient_records", False)
    monkeypatch.setattr(settings, "openrouter_model", "vendor/model:free")
    monkeypatch.setattr(settings, "ollama_model", "local-model")
    assert extraction_llm("textbook") == {"provider": "openrouter", "model": "vendor/model:free"}
    assert extraction_llm("patient_record") == {"provider": "ollama", "model": "local-model"}
