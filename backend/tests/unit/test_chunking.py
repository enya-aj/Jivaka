from jivaka.ingestion.chunking.patient_record_chunker import PatientRecordChunker
from jivaka.ingestion.chunking.textbook_chunker import TextbookChunker


def test_textbook_chunker_respects_max_chars_and_preserves_order():
    paragraphs = [f"Paragraph {i}. " + ("word " * 20) for i in range(6)]
    text = "\n\n".join(paragraphs)
    chunker = TextbookChunker(max_chunk_chars=200)

    chunks = chunker.chunk("doc-1", text)

    assert len(chunks) > 1
    assert [c.order for c in chunks] == list(range(len(chunks)))
    assert all(c.doc_id == "doc-1" for c in chunks)
    assert all(c.source_type == "textbook" for c in chunks)

    # every paragraph's content should still be present somewhere in the output
    combined = " ".join(c.text for c in chunks)
    for i in range(6):
        assert f"Paragraph {i}." in combined


def test_textbook_chunker_splits_overlong_paragraph_on_sentence_boundary():
    sentence = "This is a clinically relevant sentence about a condition. "
    long_paragraph = sentence * 10  # single paragraph, no blank-line breaks
    chunker = TextbookChunker(max_chunk_chars=150)

    chunks = chunker.chunk("doc-1", long_paragraph)

    assert len(chunks) > 1
    for chunk in chunks:
        # each block should end on a sentence boundary, not mid-word
        assert chunk.text.strip().endswith(".")


def test_patient_record_chunker_splits_on_soap_headers():
    text = (
        "Subjective:\nPatient reports headache.\n\n"
        "Objective:\nBP 130/85, HR 78.\n\n"
        "Assessment:\nTension headache.\n\n"
        "Plan:\nRecommend OTC analgesics.\n"
    )
    chunker = PatientRecordChunker(max_chunk_chars=1000)

    chunks = chunker.chunk("doc-2", text)

    assert len(chunks) == 4
    assert chunks[0].text.lower().startswith("subjective")
    assert chunks[-1].text.lower().startswith("plan")
    assert all(c.source_type == "patient_record" for c in chunks)


def test_patient_record_chunker_falls_back_to_paragraphs_without_headers():
    text = "Free-text note with no section headers.\n\nSecond paragraph of the note."
    chunker = PatientRecordChunker(max_chunk_chars=1000)

    chunks = chunker.chunk("doc-3", text)

    assert len(chunks) == 1
    assert "Free-text note" in chunks[0].text
    assert "Second paragraph" in chunks[0].text
