import hashlib
from pathlib import Path
from typing import Optional

import pymupdf as fitz
from pydantic import BaseModel

TEXT_EXTENSIONS = {".txt"}
PDF_EXTENSIONS = {".pdf"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tiff"}
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | PDF_EXTENSIONS | IMAGE_EXTENSIONS


class UnsupportedFileTypeError(ValueError):
    pass


class PageInput(BaseModel):
    page_number: int
    text: str
    # Present only for pages that could plausibly need OCR (PDF pages and raw
    # images); the OCR detector decides whether to actually use it.
    image_bytes: Optional[bytes] = None
    image_area: Optional[float] = None


class LoadedDocument(BaseModel):
    filename: str
    source_hash: str
    pages: list[PageInput]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_document(path: Path) -> LoadedDocument:
    suffix = path.suffix.lower()
    raw = path.read_bytes()
    source_hash = _sha256(raw)

    if suffix in TEXT_EXTENSIONS:
        pages = [PageInput(page_number=1, text=raw.decode("utf-8", errors="replace"))]
    elif suffix in PDF_EXTENSIONS:
        pages = _load_pdf(raw)
    elif suffix in IMAGE_EXTENSIONS:
        pages = [PageInput(page_number=1, text="", image_bytes=raw, image_area=None)]
    else:
        raise UnsupportedFileTypeError(
            f"Unsupported file type '{suffix}'. Supported: {sorted(SUPPORTED_EXTENSIONS)}"
        )

    return LoadedDocument(filename=path.name, source_hash=source_hash, pages=pages)


def _load_pdf(raw: bytes) -> list[PageInput]:
    pages: list[PageInput] = []
    with fitz.open(stream=raw, filetype="pdf") as doc:
        for i, page in enumerate(doc, start=1):
            text = page.get_text()
            pixmap = page.get_pixmap()
            pages.append(
                PageInput(
                    page_number=i,
                    text=text,
                    image_bytes=pixmap.tobytes("png"),
                    image_area=float(pixmap.width * pixmap.height),
                )
            )
    return pages
