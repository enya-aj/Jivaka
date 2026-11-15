import pytest
from fastapi.testclient import TestClient

from jivaka.api import chat_routes
from jivaka.auth import deps
from jivaka.auth.store import AppStore, ChatHistory, User, set_store
from jivaka.chat.service import ChatEvent
from jivaka.main import app


class FailingService:
    def __init__(self, *args, **kwargs):
        pass

    def answer(self, messages):
        yield ChatEvent("status", "searching")
        yield ChatEvent("sources", [])
        yield ChatEvent("token", "ไม่พบ")
        raise RuntimeError("ollama down")


class EchoService:
    """Answers with how many earlier messages it was given."""

    seen: list = []

    def __init__(self, *args, include_patients=True, **kwargs):
        self.include_patients = include_patients

    def answer(self, messages):
        EchoService.seen.append((list(messages), self.include_patients))
        yield ChatEvent("sources", [{"number": 1, "chunk_id": "c1", "source_label": "Book"}])
        yield ChatEvent("token", f"answer after {len(messages) - 1} [1]")
        yield ChatEvent("done", None)


@pytest.fixture
def store(monkeypatch):
    store = AppStore(":memory:")
    set_store(store)
    monkeypatch.setattr(chat_routes, "get_graph", lambda: None)
    EchoService.seen = []
    yield store
    set_store(None)
    app.dependency_overrides.clear()


def _as(user: User) -> None:
    app.dependency_overrides[deps.current_user] = lambda: user


DOCTOR = User(email="doc@gmail.com", name="Doc", role="doctor")
OTHER = User(email="other@gmail.com", name="Other", role="doctor")
ADMIN = User(email="boss@gmail.com", name="Boss", role="admin")
STUDENT = User(email="kid@gmail.com", name="Kid", role="student")


def _events(body: str) -> list[tuple[str, str]]:
    out = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.split("\n"))
        out.append((lines["event"], lines["data"]))
    return out


def test_chat_streams_server_sent_events(store, monkeypatch):
    monkeypatch.setattr(chat_routes, "ChatService", FailingService)

    response = TestClient(app).post("/chat", json={"question": "hi"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    body = response.text
    assert body.startswith("event: conversation\n")
    assert 'event: status\ndata: "searching"\n\n' in body
    assert 'event: token\ndata: "ไม่พบ"\n\n' in body
    assert 'event: error\ndata: "ollama down"\n\n' in body
    # A new conversation whose first answer failed isn't kept.
    assert ChatHistory(store).list_for(None) == []


def test_chat_rejects_an_empty_question(store):
    assert TestClient(app).post("/chat", json={"question": "  "}).status_code == 422


def test_conversation_is_saved_and_continued(store, monkeypatch):
    monkeypatch.setattr(chat_routes, "ChatService", EchoService)
    _as(DOCTOR)
    client = TestClient(app)

    first = _events(client.post("/chat", json={"question": "What treats pneumonia?"}).text)
    assert first[0][0] == "conversation"
    conversation_id = __import__("json").loads(first[0][1])["id"]

    client.post("/chat", json={"question": "And for children?", "conversation_id": conversation_id})
    # The second question was given the first exchange as history.
    assert [m["role"] for m in EchoService.seen[1][0]] == ["user", "assistant", "user"]

    listed = client.get("/conversations").json()
    assert [c["title"] for c in listed] == ["What treats pneumonia?"]
    saved = client.get(f"/conversations/{conversation_id}").json()
    assert [m["content"] for m in saved["messages"]] == [
        "What treats pneumonia?",
        "answer after 0 [1]",
        "And for children?",
        "answer after 2 [1]",
    ]
    assert saved["messages"][1]["sources"][0]["source_label"] == "Book"
    assert saved["read_only"] is False

    assert client.patch(f"/conversations/{conversation_id}", json={"title": "Pneumonia"}).json()["title"] == "Pneumonia"
    assert client.delete(f"/conversations/{conversation_id}").status_code == 200
    assert client.get("/conversations").json() == []


def test_chats_are_private_but_admins_can_read_them(store, monkeypatch):
    monkeypatch.setattr(chat_routes, "ChatService", EchoService)
    conversation = ChatHistory(store).create(DOCTOR.email, "private question")
    ChatHistory(store).add_exchange(conversation.id, "private question", "answer", [])
    client = TestClient(app)

    _as(OTHER)
    assert client.get("/conversations").json() == []
    assert client.get(f"/conversations/{conversation.id}").status_code == 404
    assert client.get("/conversations?everyone=true").status_code == 403
    assert client.post("/chat", json={"question": "x", "conversation_id": conversation.id}).status_code == 404
    assert client.delete(f"/conversations/{conversation.id}").status_code == 404

    _as(ADMIN)
    everyone = client.get("/conversations?everyone=true").json()
    assert [c["user_email"] for c in everyone] == [DOCTOR.email]
    saved = client.get(f"/conversations/{conversation.id}").json()
    assert saved["read_only"] is True and saved["messages"][0]["content"] == "private question"
    # Admins read others' chats but can't add to or rename them.
    assert client.post("/chat", json={"question": "x", "conversation_id": conversation.id}).status_code == 404
    assert client.patch(f"/conversations/{conversation.id}", json={"title": "x"}).status_code == 403


def test_students_chat_without_patient_records(store, monkeypatch):
    monkeypatch.setattr(chat_routes, "ChatService", EchoService)
    client = TestClient(app)
    _as(STUDENT)
    client.post("/chat", json={"question": "fever?"})
    _as(DOCTOR)
    client.post("/chat", json={"question": "fever?"})
    assert [include for _, include in EchoService.seen] == [False, True]


def test_chunk_not_found(store, monkeypatch):
    monkeypatch.setattr(chat_routes, "load_chunks", lambda graph, ids, include_patients=True: [])

    assert TestClient(app).get("/chunks/nope").status_code == 404


def test_chat_page_is_served(store):
    response = TestClient(app).get("/chat")

    assert response.status_code == 200
    assert "<title>Jivaka — Chat</title>" in response.text
