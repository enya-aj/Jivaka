from pathlib import Path

from jivaka.ingestion.intake import load_document

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


def test_load_epub_extracts_chapter_text():
    loaded = load_document(FIXTURES_DIR / "sample_ebook.epub")

    assert loaded.filename == "sample_ebook.epub"
    assert len(loaded.pages) >= 1
    combined = " ".join(page.text for page in loaded.pages)
    assert "hypertension" in combined.lower()
    assert "metformin" in combined.lower()
    # Ebook text is clean by construction - no image to (mis)route through OCR.
    assert all(page.image_bytes is None for page in loaded.pages)
