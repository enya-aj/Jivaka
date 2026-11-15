from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from jivaka.api import auth_routes, routes
from jivaka.auth import deps, google
from jivaka.auth.store import AppStore, set_store
from jivaka.config import Settings
from jivaka.main import app


@pytest.fixture
def settings():
    return Settings(
        auth_enabled=True,
        google_client_id="client-id",
        google_client_secret="secret",
        public_base_url="https://jivaka.example",
        admin_emails="Boss@Gmail.com",
        cookie_secure=False,
        app_db_path=":memory:",
    )


@pytest.fixture
def store(monkeypatch, settings):
    store = AppStore(":memory:")
    set_store(store)
    monkeypatch.setattr(deps, "get_settings", lambda: settings)
    monkeypatch.setattr(auth_routes, "get_settings", lambda: settings)
    yield store
    set_store(None)


def _sign_in(client: TestClient, monkeypatch, email: str, name: str = "Someone"):
    async def fake_identity(_settings, code):
        assert code == "the-code"
        return google.GoogleIdentity(email=email, name=name)

    monkeypatch.setattr(google, "fetch_identity", fake_identity)
    start = client.get("/auth/google", follow_redirects=False)
    assert start.status_code == 303
    assert start.headers["location"].startswith(google.AUTHORIZE_URL)
    state = client.cookies.get(auth_routes.STATE_COOKIE)
    return client.get(f"/auth/callback?code=the-code&state={state}", follow_redirects=False)


def test_pages_and_api_need_sign_in(store):
    client = TestClient(app)
    page = client.get("/", follow_redirects=False)
    assert page.status_code == 303 and page.headers["location"] == "/login"
    assert client.get("/documents").status_code == 401
    assert client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]}).status_code == 401
    assert client.get("/health").status_code == 200
    assert client.get("/login").status_code == 200


def test_admin_email_signs_in_without_invite(store, monkeypatch):
    client = TestClient(app)
    response = _sign_in(client, monkeypatch, "boss@gmail.com", "Boss")
    assert response.status_code == 303 and response.headers["location"] == "/"
    me = client.get("/me").json()
    assert me["role"] == "admin" and me["name"] == "Boss"
    assert store.get_user("boss@gmail.com").last_login_at is not None


def test_uninvited_account_is_refused(store, monkeypatch):
    client = TestClient(app)
    response = _sign_in(client, monkeypatch, "stranger@gmail.com")
    assert response.headers["location"] == "/login?error=not_invited"
    assert client.get("/me").status_code == 401


def test_state_mismatch_is_refused(store, monkeypatch):
    client = TestClient(app)
    client.get("/auth/google", follow_redirects=False)
    response = client.get("/auth/callback?code=x&state=wrong", follow_redirects=False)
    assert response.headers["location"] == "/login?error=expired"


def test_invited_student_is_limited(store, monkeypatch):
    store.add_user("student@gmail.com", "student")
    client = TestClient(app)
    _sign_in(client, monkeypatch, "student@gmail.com")

    assert client.get("/", follow_redirects=False).headers["location"] == "/chat"
    assert client.get("/chat").status_code == 200
    upload = client.post("/ingest", files={"file": ("a.txt", b"x", "text/plain")}, data={"doc_type": "textbook"})
    assert upload.status_code == 403
    assert client.get("/admin/users").status_code == 403
    assert client.delete("/documents/some-id").status_code == 403


def test_students_never_see_patient_documents(store, monkeypatch):
    store.add_user("student@gmail.com", "student")
    docs = [{"id": "1", "doc_type": "textbook"}, {"id": "2", "doc_type": "patient_record"}]
    monkeypatch.setattr(routes, "get_graph", lambda: None)
    monkeypatch.setattr(routes, "list_documents", lambda graph, limit: [dict(d) for d in docs])
    client = TestClient(app)
    _sign_in(client, monkeypatch, "student@gmail.com")
    assert [d["id"] for d in client.get("/documents").json()] == ["1"]


def test_admin_manages_invites(store, monkeypatch):
    client = TestClient(app)
    _sign_in(client, monkeypatch, "boss@gmail.com")

    added = client.post("/admin/users", json={"email": " Doc@Gmail.com ", "role": "doctor"})
    assert added.status_code == 200 and added.json()["email"] == "doc@gmail.com"
    assert client.post("/admin/users", json={"email": "doc@gmail.com", "role": "doctor"}).status_code == 409
    assert client.post("/admin/users", json={"email": "nope", "role": "doctor"}).status_code == 422

    assert client.patch("/admin/users/doc@gmail.com", json={"role": "student"}).json()["role"] == "student"
    assert client.patch("/admin/users/boss@gmail.com", json={"role": "doctor"}).status_code == 400
    assert client.delete("/admin/users/boss@gmail.com").status_code == 400
    assert client.delete("/admin/users/doc@gmail.com").status_code == 200
    assert [u["email"] for u in client.get("/admin/users").json()] == ["boss@gmail.com"]


def test_switched_off_user_loses_session(store, monkeypatch):
    store.add_user("doc@gmail.com", "doctor")
    client = TestClient(app)
    _sign_in(client, monkeypatch, "doc@gmail.com")
    assert client.get("/me").status_code == 200
    store.update_user("doc@gmail.com", active=False)
    assert client.get("/me").status_code == 401


def test_logout_ends_session(store, monkeypatch):
    store.add_user("doc@gmail.com", "doctor")
    client = TestClient(app)
    _sign_in(client, monkeypatch, "doc@gmail.com")
    response = client.post("/auth/logout", follow_redirects=False)
    assert response.headers["location"] == "/login"
    assert client.get("/me").status_code == 401


def test_sign_in_not_configured(store, monkeypatch, settings):
    blank = settings.model_copy(update={"google_client_id": ""})
    monkeypatch.setattr(auth_routes, "get_settings", lambda: blank)
    response = TestClient(app).get("/auth/google", follow_redirects=False)
    assert response.headers["location"] == "/login?error=not_configured"


def test_google_redirect_uri_uses_public_base_url(settings):
    url = google.authorize_url(settings, "s")
    assert "redirect_uri=https%3A%2F%2Fjivaka.example%2Fauth%2Fcallback" in url
    assert "scope=openid+email+profile" in url


def test_students_search_skips_patient_chunks():
    from jivaka.retrieval import graph_search

    queries = []

    class FakeGraph:
        def query(self, cypher, params=None):
            queries.append(cypher)
            return SimpleNamespace(result_set=[])

    graph_search.search(FakeGraph(), ["fever"], ["P-0001"], include_patients=False)
    assert queries and all("deidentified" not in q for q in queries if "Chunk" in q)
    assert not any("pseudonym" in q for q in queries)
