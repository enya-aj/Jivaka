import hashlib
import shutil
from pathlib import Path
from typing import Optional

import ebooklib
import mobi
import pymupdf as fitz
from bs4 import BeautifulSoup
from ebooklib import epub
from pydantic import BaseModel

TEXT_EXTENSIONS = {".txt"}
PDF_EXTENSIONS = {".pdf"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tiff"}
EBOOK_EXTENSIONS = {".epub", ".mobi"}
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | PDF_EXTENSIONS | IMAGE_EXTENSIONS | EBOOK_EXTENSIONS


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
    elif suffix == ".epub":
        pages = _load_epub(path)
    elif suffix == ".mobi":
        pages = _load_mobi(path)
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


def _html_to_text(html: bytes | str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    return soup.get_text(separator="\n").strip()


def _load_epub(path: Path) -> list[PageInput]:
    # Ebook text is already clean (no scan involved), so pages carry no
    # image_bytes/image_area - the pipeline's OCR-fallback check has nothing
    # to OCR against for these and leaves the extracted text as-is.
    book = epub.read_epub(str(path))
    pages: list[PageInput] = []
    for item in book.get_items():
        if item.get_type() != ebooklib.ITEM_DOCUMENT:
            continue
        text = _html_to_text(item.get_content())
        if text:
            pages.append(PageInput(page_number=len(pages) + 1, text=text))
    return pages


def _load_mobi(path: Path) -> list[PageInput]:
    tempdir, extracted_path = mobi.extract(str(path))
    try:
        extracted = Path(extracted_path)
        if extracted.suffix.lower() == ".epub":
            return _load_epub(extracted)

        # Older MOBI files unpack to loose HTML rather than an .epub - gather
        # and concatenate every HTML file found, in a stable order.
        pages: list[PageInput] = []
        for html_file in sorted(Path(tempdir).rglob("*.htm*")):
            text = _html_to_text(html_file.read_text(encoding="utf-8", errors="replace"))
            if text:
                pages.append(PageInput(page_number=len(pages) + 1, text=text))
        return pages
    finally:
        shutil.rmtree(tempdir, ignore_errors=True)
