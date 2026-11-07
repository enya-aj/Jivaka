import json

import httpx
import pytest

from jivaka.ingestion.extraction.llm_client import OllamaClient
from jivaka.ingestion.extraction.llm_extractor import LLMEntityExtractor
from jivaka.ingestion.models import Chunk


def client_with(handler):
    client = OllamaClient(host="http://ollama", model="m")
    client._client = httpx.Client(base_url="http://ollama", transport=httpx.MockTransport(handler))
    return client


def ndjson(*objs):
    return "\n".join(json.dumps(o) for o in objs) + "\n"


def test_generate_json_joins_streamed_pieces():
    def handler(request):
        body = json.loads(request.content)
        assert request.url.path == "/api/generate"
        assert body["format"] == "json" and body["stream"] is True
        return httpx.Response(200, text=ndjson(
            {"response": '{"entities": [', "done": False},
            {"response": "]}", "done": False},
            {"response": "", "done": True},
        ))

    assert client_with(handler).generate_json("p") == {"entities": []}


def test_generate_json_pulls_a_missing_model_once():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/api/pull":
            return httpx.Response(200, json={"status": "success"})
        if calls.count("/api/generate") == 1:
            return httpx.Response(404, json={"error": "model not found"})
        return httpx.Response(200, text=ndjson({"response": "{}", "done": True}))

    assert client_with(handler).generate_json("p") == {}
    assert calls == ["/api/generate", "/api/pull", "/api/generate"]


def test_generate_json_raises_on_stream_error():
    def handler(request):
        return httpx.Response(200, text=ndjson({"error": "out of memory"}))

    with pytest.raises(RuntimeError, match="out of memory"):
        client_with(handler).generate_json("p")


class FlakyClient:
    def __init__(self, failures):
        self.failures = failures
        self.calls = 0

    def generate_json(self, prompt):
        self.calls += 1
        if self.calls <= self.failures:
            raise httpx.ReadTimeout("slow")
        return {"entities": [{"name": "Fever", "entity_type": "symptom"}], "relations": []}


def _chunk():
    return Chunk(doc_id="d", text="fever", order=0, source_type="textbook")


def test_extractor_retries_a_failed_call(monkeypatch):
    monkeypatch.setattr("jivaka.ingestion.extraction.llm_extractor.time.sleep", lambda s: None)
    client = FlakyClient(failures=1)

    extraction = LLMEntityExtractor(client=client, retries=1).extract(_chunk())

    assert client.calls == 2
    assert not extraction.failed
    assert [e.normalized_name for e in extraction.entities] == ["fever"]


def test_extractor_marks_the_chunk_failed_after_the_last_retry(monkeypatch):
    monkeypatch.setattr("jivaka.ingestion.extraction.llm_extractor.time.sleep", lambda s: None)
    client = FlakyClient(failures=5)

    extraction = LLMEntityExtractor(client=client, retries=1).extract(_chunk())

    assert client.calls == 2
    assert extraction.failed and extraction.entities == []
