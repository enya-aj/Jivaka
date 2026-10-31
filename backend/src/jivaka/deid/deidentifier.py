import re
from typing import Iterable, Optional, Sequence

from jivaka.deid.ner import NerBackend
from jivaka.deid.patterns import PERSON, Span, find_pattern_spans
from jivaka.ingestion.chunking.patient_record_chunker import SECTION_HEADER_RE

_THAI_TITLE_PREFIX_RE = re.compile(
    r"^(?:นางสาว|เด็กชาย|เด็กหญิง|ทพญ\.|น\.ส\.|ด\.ช\.|ด\.ญ\.|นพ\.|พญ\.|ทพ\.|ภก\.|ภญ\.|พว\.|ดร\.|"
    r"ผศ\.|รศ\.|นาย|นาง)"
)
_THAI_WORD_RE = re.compile("^[฀-๿]{3,}$")
_LATIN_NAME_RE = re.compile(r"^[A-Z][A-Za-z'\-]{2,}$")
# Words NER sometimes pulls into a person span that must not then be
# redacted everywhere else in the record.
_NOT_NAME_WORDS = {"Patient", "Doctor", "Name", "Mrs", "Miss", "Prof", "The", "And", "With", "Hospital"}


def _canonical(name: str) -> str:
    words = [_THAI_TITLE_PREFIX_RE.sub("", w) for w in name.split()]
    return " ".join(w for w in words if w)


def _name_words(name: str) -> list[str]:
    return [
        w
        for w in _canonical(name).split()
        if (_THAI_WORD_RE.match(w) or _LATIN_NAME_RE.match(w)) and w not in _NOT_NAME_WORDS
    ]


def _merge(spans: Iterable[Span]) -> list[Span]:
    merged: list[Span] = []
    for span in sorted(spans, key=lambda s: (s.start, -s.end)):
        if merged and span.start < merged[-1].end:
            last = merged[-1]
            if span.end > last.end:
                longer = span if (span.end - span.start) > (last.end - last.start) else last
                merged[-1] = Span(last.start, span.end, longer.kind)
            continue
        merged.append(span)
    return merged


def _split_lines(text: str, spans: Iterable[Span]) -> list[Span]:
    """NER spans can run across a line break and swallow the next line (a
    section header such as "Assessment:", which the patient record chunker
    needs). Each line's part of a span is kept as its own span instead, and
    a line that is a section header is never redacted."""
    result = []
    for span in spans:
        start = span.start
        for line in text[span.start : span.end].split("\n"):
            stripped = line.strip()
            if stripped and not SECTION_HEADER_RE.fullmatch(line):
                offset = start + line.index(stripped)
                result.append(Span(offset, offset + len(stripped), span.kind))
            start += len(line) + 1
    return result


class Deidentifier:
    """Replaces identifiers in one patient document's text with placeholders.

    Names become numbered placeholders that stay consistent across the texts
    passed in one call ("[PERSON-1]" is the same person everywhere), and a
    name found once - after a title, a "Name:" label, or by NER - is also
    removed wherever its words appear again without that cue. Everything
    else becomes a typed placeholder: [DATE], [PHONE], [EMAIL], [ID],
    [ADDRESS], [PLACE].
    """

    def __init__(self, ner_backends: Sequence[NerBackend] = (), extra_id_patterns: Sequence[str] = ()):
        self.ner_backends = list(ner_backends)
        self.extra_id_patterns = [re.compile(p) for p in extra_id_patterns]

    def deidentify(self, text: str) -> str:
        return self.deidentify_texts([text])[0]

    def deidentify_texts(self, texts: Sequence[str]) -> list[str]:
        detected = [self._detect(text) for text in texts]

        person_ids: dict[str, int] = {}
        word_ids: dict[str, int] = {}
        for text, spans in zip(texts, detected):
            for span in spans:
                if span.kind == PERSON:
                    self._register_name(text[span.start : span.end], person_ids, word_ids)

        name_res = [
            (re.compile(rf"\b{re.escape(w)}\b") if w.isascii() else re.compile(re.escape(w)))
            for w in sorted(word_ids, key=len, reverse=True)
        ]
        results = []
        for text, spans in zip(texts, detected):
            spans = list(spans)
            for regex in name_res:
                spans += [Span(m.start(), m.end(), PERSON) for m in regex.finditer(text)]
            results.append(self._replace(text, _merge(spans), person_ids, word_ids))
        return results

    def _detect(self, text: str) -> list[Span]:
        spans = find_pattern_spans(text, self.extra_id_patterns)
        for backend in self.ner_backends:
            spans += backend.find(text)
        return _split_lines(text, spans)

    @staticmethod
    def _register_name(name: str, person_ids: dict[str, int], word_ids: dict[str, int]) -> int:
        canonical = _canonical(name)
        words = _name_words(name)
        person_id = person_ids.get(canonical)
        if person_id is None:
            person_id = next((word_ids[w] for w in words if w in word_ids), None)
        if person_id is None:
            person_id = max(person_ids.values(), default=0) + 1
        person_ids.setdefault(canonical, person_id)
        for w in words:
            word_ids.setdefault(w, person_id)
        return person_id

    def _replace(
        self, text: str, spans: list[Span], person_ids: dict[str, int], word_ids: dict[str, int]
    ) -> str:
        out = []
        pos = 0
        for span in spans:
            out.append(text[pos : span.start])
            if span.kind == PERSON:
                person_id = self._register_name(text[span.start : span.end], person_ids, word_ids)
                out.append(f"[PERSON-{person_id}]")
            else:
                out.append(f"[{span.kind}]")
            pos = span.end
        out.append(text[pos:])
        return "".join(out)


def build_deidentifier(use_ner: bool = True, extra_id_patterns: Optional[Sequence[str]] = None) -> Deidentifier:
    backends: list[NerBackend] = []
    if use_ner:
        from jivaka.deid.ner import get_english_ner, get_thai_ner

        backends = [get_english_ner(), get_thai_ner()]
    return Deidentifier(backends, extra_id_patterns or ())
