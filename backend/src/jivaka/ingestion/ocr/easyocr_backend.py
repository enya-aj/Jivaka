from functools import lru_cache


@lru_cache
def _get_reader():
    # Imported lazily: easyocr pulls in torch and is only needed once a page
    # actually requires OCR, so plain-text ingestion never pays for it.
    import easyocr

    # gpu=False: this pipeline targets a general Docker deployment with no
    # guaranteed GPU. Model weights are cached in the easyocr_models volume
    # (see docker-compose.yml) so this only downloads once.
    return easyocr.Reader(["en"], gpu=False)


def ocr_image(image_bytes: bytes) -> str:
    """Runs local, offline OCR over a single page/image and returns the
    recognized text, paragraph-joined."""
    reader = _get_reader()
    paragraphs = reader.readtext(image_bytes, detail=0, paragraph=True)
    return "\n".join(paragraphs)
