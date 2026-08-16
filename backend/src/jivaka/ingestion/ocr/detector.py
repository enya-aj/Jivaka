from typing import Optional


def needs_ocr(text: str, image_area: Optional[float], threshold: float) -> bool:
    """Decides whether a page's extracted text layer is too sparse to trust,
    and OCR should run instead.

    `image_area` is the page's pixel area (width * height) when a rendered
    image is available (PDF pages, raw images); `None` means there is no
    image to fall back on (e.g. an image failed to render), which we also
    treat as needing OCR since we have nothing better to check.
    """
    stripped = text.strip()
    if image_area is None:
        return True
    if image_area <= 0:
        return True
    if not stripped:
        return True
    density = len(stripped) / image_area
    return density < threshold
