from jivaka.deid import Deidentifier
from jivaka.deid.patterns import PERSON, PLACE, Span


class FakeNer:
    """Tags fixed substrings, standing in for the spaCy/PyThaiNLP models."""

    def __init__(self, tagged: dict[str, str]):
        self.tagged = tagged

    def find(self, text):
        spans = []
        for words, kind in self.tagged.items():
            start = text.find(words)
            if start >= 0:
                spans.append(Span(start, start + len(words), kind))
        return spans


def test_english_identifiers_are_replaced():
    text = (
        "Name: John Smith\n"
        "HN: 6512345  AN 67-001234\n"
        "DOB 03/04/1960. Seen by Dr. Alice Wong on March 3, 2024.\n"
        "Phone 081-234-5678, email john.smith@example.com, ID 1-2345-67890-12-3.\n"
        "Address: 12 Sukhumvit Rd, Khlong Toei\n"
    )

    out = Deidentifier().deidentify(text)

    for identifier in ["John", "Smith", "6512345", "67-001234", "1960", "Alice", "Wong", "March 3",
                       "081-234-5678", "example.com", "1-2345-67890-12-3", "Sukhumvit"]:
        assert identifier not in out
    assert "Name: [PERSON-1]" in out
    assert "Dr. [PERSON-2]" in out
    assert "HN: [ID]" in out
    assert "[DATE]" in out and "[PHONE]" in out and "[EMAIL]" in out and "[ADDRESS]" in out


def test_thai_identifiers_are_replaced():
    text = (
        "ผู้ป่วยชื่อ นางสาวมาลี วงศ์ใหญ่ HN 12345 มาตรวจวันที่ 3 มีนาคม 2567 โทร 02-123-4567\n"
        "นายแพทย์ตรวจพบความดันโลหิตสูง เลขบัตรประชาชน 1234567890123\n"
    )

    out = Deidentifier().deidentify(text)

    for identifier in ["มาลี", "วงศ์ใหญ่", "12345", "มีนาคม 2567", "02-123-4567", "1234567890123"]:
        assert identifier not in out
    # นายแพทย์ (doctor) starts with the title นาย but isn't a name.
    assert "นายแพทย์ตรวจพบความดันโลหิตสูง" in out


def test_a_name_found_once_is_removed_everywhere_with_the_same_number():
    texts = ["Mr. John Smith was admitted.", "Smith reports less pain.", "John is discharged."]

    out = Deidentifier().deidentify_texts(texts)

    assert out == ["Mr. [PERSON-1] was admitted.", "[PERSON-1] reports less pain.", "[PERSON-1] is discharged."]


def test_thai_name_without_title_is_removed_after_being_seen_with_one():
    out = Deidentifier().deidentify("นางสาวมาลี วงศ์ใหญ่ มาตรวจ\nมาลี มีอาการไข้")

    assert "มาลี" not in out
    assert "มีอาการไข้" in out


def test_ner_spans_do_not_swallow_section_headers():
    text = "Lives near Khlong Toei\nAssessment:\nStable."
    ner = FakeNer({"Khlong Toei\nAssessment": PLACE})

    out = Deidentifier([ner]).deidentify(text)

    assert out == "Lives near [PLACE]\nAssessment:\nStable."


def test_clinical_content_is_kept():
    text = "Start amoxicillin 500mg three times daily for 7 days. BP 120/80, HR 102, SpO2 94%."

    assert Deidentifier().deidentify(text) == text


def test_ner_person_spans_are_numbered_and_merged_with_rules():
    ner = FakeNer({"Somchai": PERSON})

    out = Deidentifier([ner]).deidentify("Somchai was seen by Dr. Somchai Jaidee.")

    assert out == "[PERSON-1] was seen by Dr. [PERSON-1]."


def test_extra_id_patterns():
    out = Deidentifier(extra_id_patterns=[r"\bXR-\d{5}\b"]).deidentify("Film XR-12345 reviewed.")

    assert out == "Film [ID] reviewed."


def test_real_ner_models_keep_clinical_durations():
    import pytest

    from jivaka.deid import build_deidentifier

    try:
        deidentifier = build_deidentifier(use_ner=True)
        deidentifier.deidentify("นายสมชาย ใจดี")
    except Exception as exc:  # noqa: BLE001 - models are baked into the Docker image only
        pytest.skip(f"NER models not installed: {exc}")

    out = deidentifier.deidentify(
        "Somchai Jaidee has had fever for 3 days; amoxicillin for 7 days.\n"
        "นายสมชาย ใจดี มีไข้มา 3 วัน"
    )

    assert "Somchai" not in out and "สมชาย" not in out
    assert "for 3 days" in out and "for 7 days" in out and "3 วัน" in out
