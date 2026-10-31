from jivaka.chat import service as service_module
from jivaka.chat.service import NO_SOURCES_EN, NO_SOURCES_TH, ChatService
from jivaka.retrieval.graph_search import RetrievedChunk


class FakeLLM:
    def __init__(self, answer="Amoxicillin [1].", terms=None):
        self.answer = answer
        self.terms = terms or []
        self.chat_calls = []
        self.json_calls = []

    def chat_stream(self, messages):
        self.chat_calls.append(messages)
        yield from self.answer.split(" ")

    def generate_json(self, prompt):
        self.json_calls.append(prompt)
        return {"terms": self.terms}


def chunk(chunk_id="c1"):
    return RetrievedChunk(
        chunk_id=chunk_id, doc_id="d1", doc_type="textbook", source_label="pulmonology.pdf",
        text="Pneumonia is treated with amoxicillin.", order=0, page_number=12,
    )


def fake_search(results_by_call):
    calls = []

    def search(graph, terms, pseudonyms, top_k):
        calls.append((list(terms), list(pseudonyms)))
        return results_by_call[min(len(calls), len(results_by_call)) - 1]

    return search, calls


def test_answer_streams_status_sources_tokens_done(monkeypatch):
    search, _ = fake_search([[chunk()]])
    monkeypatch.setattr(service_module, "search", search)
    llm = FakeLLM()

    events = list(ChatService(graph=None, llm=llm).answer([{"role": "user", "content": "How is pneumonia treated?"}]))

    types = [e.type for e in events]
    assert types[:3] == ["status", "sources", "status"]
    assert types[-1] == "done"
    assert "".join(e.data for e in events if e.type == "token") == "Amoxicillin[1]."
    sources = events[1].data
    assert sources[0]["number"] == 1 and sources[0]["source_label"] == "pulmonology.pdf"
    prompt = llm.chat_calls[0][-1]["content"]
    assert "[1] pulmonology.pdf, page 12" in prompt
    assert prompt.endswith("Question: How is pneumonia treated?")


def test_no_sources_answers_without_calling_the_model(monkeypatch):
    search, _ = fake_search([[]])
    monkeypatch.setattr(service_module, "search", search)
    llm = FakeLLM()

    events = list(ChatService(graph=None, llm=llm).answer([{"role": "user", "content": "What is xyzzy?"}]))

    assert [e.data for e in events if e.type == "token"] == [NO_SOURCES_EN]
    assert llm.chat_calls == []


def test_thai_question_adds_translated_terms_and_answers_in_thai_when_empty(monkeypatch):
    search, calls = fake_search([[]])
    monkeypatch.setattr(service_module, "search", search)
    llm = FakeLLM(terms=["Dengue fever"])

    events = list(ChatService(graph=None, llm=llm).answer([{"role": "user", "content": "ไข้เลือดออกรักษาอย่างไร"}]))

    assert "dengue fever" in calls[0][0]
    assert [e.data for e in events if e.type == "token"] == [NO_SOURCES_TH]


def test_follow_up_reuses_earlier_patient_and_terms(monkeypatch):
    search, calls = fake_search([[], [chunk()]])
    monkeypatch.setattr(service_module, "search", search)
    history = [
        {"role": "user", "content": "Does P-0042 have pneumonia?"},
        {"role": "assistant", "content": "Yes [1]."},
        {"role": "user", "content": "And what was given for it?"},
    ]

    list(ChatService(graph=None, llm=FakeLLM()).answer(history))

    assert calls[0][1] == ["P-0042"]
    assert "pneumonia" in calls[1][0]


def test_pseudonym_only_question_falls_back_to_that_patients_records(monkeypatch):
    search, calls = fake_search([[], [chunk()]])
    monkeypatch.setattr(service_module, "search", search)

    list(ChatService(graph=None, llm=FakeLLM()).answer([{"role": "user", "content": "Summarise P-0001"}]))

    assert calls[-1] == ([], ["P-0001"])
