import re

from jivaka.ingestion.chunking.base import (
    Chunker,
    group_by_max_chars,
    split_paragraphs,
    to_chunks,
)
from jivaka.ingestion.models import Chunk

# Common clinical note section headers (SOAP and beyond). Placeholder set,
# not exhaustive - expected to grow once real sample documents are available.
SECTION_HEADER_RE = re.compile(
    r"^\s*(subjective|objective|assessment|plan|chief complaint|"
    r"history of present illness|hpi|past medical history|pmh|"
    r"medications|allergies|review of systems|ros|"
    r"physical exam(?:ination)?|labs?|impression|diagnosis|treatment plan)\s*:?\s*$",
    re.IGNORECASE | re.MULTILINE,
)

MAX_CHUNK_CHARS = 1200


class PatientRecordChunker(Chunker):
    source_type = "patient_record"

    def __init__(self, max_chunk_chars: int = MAX_CHUNK_CHARS):
        self.max_chunk_chars = max_chunk_chars

    def chunk(self, doc_id: str, text: str) -> list[Chunk]:
        sections = self._split_by_headers(text)
        if len(sections) > 1:
            # Each detected section is its own chunk boundary - sections carry
            # clinical meaning (SOAP structure) and shouldn't be merged with
            # neighbors. An individual over-long section is still split
            # further, just never combined with another section.
            blocks: list[str] = []
            for section in sections:
                blocks.extend(group_by_max_chars([section], self.max_chunk_chars))
        else:
            blocks = group_by_max_chars(split_paragraphs(text), self.max_chunk_chars)
        return to_chunks(doc_id, blocks, self.source_type)

    def _split_by_headers(self, text: str) -> list[str]:
        matches = list(SECTION_HEADER_RE.finditer(text))
        if not matches:
            return []
        sections: list[str] = []
        for i, match in enumerate(matches):
            start = match.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            section = text[start:end].strip()
            if section:
                sections.append(section)
        return sections
