from fastapi.testclient import TestClient

from jivaka.api import chat_routes
from jivaka.chat.service import ChatEvent
from jivaka.main import app


class FakeService:
    def __init__(self, *args, **kwargs):
        pass

    def answer(self, messages):
        yield ChatEvent("status", "searching")
        yield ChatEvent("sources", [])
        yield ChatEvent("token", "ไม่พบ")
        raise RuntimeError("ollama down")


def test_chat_streams_server_sent_events(monkeypatch):
    monkeypatch.setattr(chat_routes, "ChatService", FakeService)
    monkeypatch.setattr(chat_routes, "get_graph", lambda: None)

    response = TestClient(app).post("/chat", json={"messages": [{"role": "user", "content": "hi"}]})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    body = response.text
    assert 'event: status\ndata: "searching"\n\n' in body
    assert 'event: token\ndata: "ไม่พบ"\n\n' in body
    assert 'event: error\ndata: "ollama down"\n\n' in body


def test_chat_rejects_a_conversation_not_ending_in_a_question():
    response = TestClient(app).post("/chat", json={"messages": [{"role": "assistant", "content": "hi"}]})

    assert response.status_code == 422


def test_chunk_not_found(monkeypatch):
    monkeypatch.setattr(chat_routes, "get_graph", lambda: None)
    monkeypatch.setattr(chat_routes, "load_chunks", lambda graph, ids: [])

    assert TestClient(app).get("/chunks/nope").status_code == 404


def test_chat_page_is_served():
    response = TestClient(app).get("/chat")

    assert response.status_code == 200
    assert "<title>Jivaka — Chat</title>" in response.text
