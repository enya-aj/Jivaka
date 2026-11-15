from jivaka.ingestion.chunking.selector import get_chunker
from jivaka.ingestion.models import is_patient_doc
from jivaka.transcripts import summary as summary_module
from jivaka.transcripts.summary import summarize_transcript


class FakeLLM:
    model = "fake-model"

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    def generate_json(self, prompt):
        self.prompts.append(prompt)
        return self.replies.pop(0)


SOAP = {
    "language": "th",
    "subjective": "ไข้ 3 วัน",
    "objective": ["T 38.5", "BP 120/80"],
    "assessment": "Dengue fever (ไข้เลือดออก) suspected",
    "plan": "CBC, paracetamol",
    "plain_summary": "ผู้ป่วยมีไข้",
}


def test_transcripts_are_patient_documents():
    assert is_patient_doc("patient_transcript") and is_patient_doc("patient_record")
    assert not is_patient_doc("textbook")


def test_transcript_chunker_keeps_turns_whole():
    turns = [f"Doctor: question {i} " + "x" * 50 for i in range(30)]
    chunks = get_chunker("patient_transcript", 300).chunk("doc", "\n".join(turns))
    assert len(chunks) > 1
    assert all(c.source_type == "patient_transcript" for c in chunks)
    rebuilt = [line for c in chunks for line in c.text.split("\n")]
    assert rebuilt == turns


def test_short_transcript_is_summarized_in_one_call():
    llm = FakeLLM([SOAP])
    result = summarize_transcript("หมอ: เป็นอะไรมาครับ\nผู้ป่วย: ไข้ 3 วันค่ะ", llm)
    assert len(llm.prompts) == 1 and "ไข้ 3 วันค่ะ" in llm.prompts[0]
    assert result["language"] == "th"
    assert result["objective"] == "- T 38.5\n- BP 120/80"
    assert result["plain_summary"] == "ผู้ป่วยมีไข้"


def test_long_transcript_is_summarized_from_points(monkeypatch):
    monkeypatch.setattr(summary_module, "PART_CHARS", 100)
    text = "\n".join(f"Patient: symptom number {i} started last week" for i in range(10))
    parts = len(summary_module._split(text, 100))
    llm = FakeLLM([{"points": [f"point {i}"]} for i in range(parts)] + [{**SOAP, "language": "en"}])
    result = summarize_transcript(text, llm)
    assert len(llm.prompts) == parts + 1
    assert "- point 0" in llm.prompts[-1] and f"- point {parts - 1}" in llm.prompts[-1]
    assert result["language"] == "en"


def test_empty_transcript_is_an_error():
    import pytest

    with pytest.raises(ValueError):
        summarize_transcript("  \n ", FakeLLM([]))
