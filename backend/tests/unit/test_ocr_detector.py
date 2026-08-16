import pytest

from jivaka.ingestion.ocr.detector import needs_ocr


@pytest.mark.parametrize(
    ("text", "image_area", "threshold", "expected"),
    [
        ("some text here", None, 0.005, True),  # no image area -> can't judge, OCR
        ("some text here", 0, 0.005, True),  # zero area -> OCR
        ("", 20000, 0.005, True),  # empty text layer -> OCR
        ("   \n  ", 20000, 0.005, True),  # whitespace-only text layer -> OCR
        ("x" * 300, 20000, 0.005, False),  # dense text (density 0.015) -> no OCR
        ("x" * 50, 20000, 0.005, True),  # sparse text (density 0.0025) -> OCR
    ],
)
def test_needs_ocr(text, image_area, threshold, expected):
    assert needs_ocr(text, image_area, threshold) is expected
