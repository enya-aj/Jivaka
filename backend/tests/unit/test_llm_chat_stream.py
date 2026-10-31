import json

import httpx
import pytest

from jivaka.ingestion.extraction.llm_client import OllamaClient


def client_with(handler):
    client = OllamaClient(host="http://ollama", model="m")
    client._client = httpx.Client(base_url="http://ollama", transport=httpx.MockTransport(handler))
    return client


def ndjson(*objs):
    return "\n".join(json.dumps(o) for o in objs) + "\n"


def test_chat_stream_yields_message_pieces():
    def handler(request):
        body = json.loads(request.content)
        assert request.url.path == "/api/chat"
        assert body["model"] == "m" and body["stream"] is True
        return httpx.Response(200, text=ndjson(
            {"message": {"content": "Hel"}, "done": False},
            {"message": {"content": "lo"}, "done": False},
            {"message": {"content": ""}, "done": True},
        ))

    assert list(client_with(handler).chat_stream([{"role": "user", "content": "hi"}])) == ["Hel", "lo"]


def test_chat_stream_pulls_a_missing_model_once():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/api/pull":
            return httpx.Response(200, json={"status": "success"})
        if calls.count("/api/chat") == 1:
            return httpx.Response(404, json={"error": "model not found"})
        return httpx.Response(200, text=ndjson({"message": {"content": "ok"}, "done": True}))

    assert list(client_with(handler).chat_stream([])) == ["ok"]
    assert calls == ["/api/chat", "/api/pull", "/api/chat"]


def test_chat_stream_raises_on_an_error_line():
    def handler(request):
        return httpx.Response(200, text=ndjson({"error": "out of memory"}))

    with pytest.raises(RuntimeError, match="out of memory"):
        list(client_with(handler).chat_stream([]))
