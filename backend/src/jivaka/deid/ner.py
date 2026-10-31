"""Named-entity backends for names, places and dates the regex rules can't
see: spaCy for English, PyThaiNLP's ThaiNER for Thai. Both models are
downloaded at image build time (see backend/Dockerfile); a missing model
raises rather than silently skipping de-identification."""

import re
from functools import lru_cache
from typing import Protocol

from jivaka.deid.patterns import PERSON, PLACE, Span

_THAI_CHAR_RE = re.compile("[฀-๿]")
_LATIN_WORD_RE = re.compile("[A-Za-z]{2,}")

# Eponymous medical terms ("Parkinson's disease", "Babinski sign") are tagged
# as people by general-purpose NER; a person span followed by one of these
# words is kept.
_EPONYM_FOLLOWER_RE = re.compile(
    r"^(?:'s)?\s+(?:disease|syndrome|sign|test|reflex|palsy|maneuver|manoeuvre|procedure|"
    r"operation|criteria|scale|score|classification|fracture|tumou?r|lymphoma|an(?:a)?emia|"
    r"ulcer|law|equation|triad|phenomenon|node|cell)",
    re.IGNORECASE,
)


class NerBackend(Protocol):
    def find(self, text: str) -> list[Span]: ...


class SpacyEnglishNer:
    # No DATE: spaCy tags durations ("3 days", "for 7 days") as dates, and
    # those are clinical facts. Real dates are caught by the regex rules.
    _KINDS = {"PERSON": PERSON, "GPE": PLACE, "LOC": PLACE, "FAC": PLACE}

    def __init__(self, model: str = "en_core_web_sm"):
        import spacy

        self._nlp = spacy.load(model, disable=["lemmatizer"])

    def find(self, text: str) -> list[Span]:
        if not _LATIN_WORD_RE.search(text):
            return []
        spans = []
        for ent in self._nlp(text).ents:
            kind = self._KINDS.get(ent.label_)
            if kind is None:
                continue
            if kind == PERSON and _EPONYM_FOLLOWER_RE.match(text[ent.end_char :]):
                continue
            spans.append(Span(ent.start_char, ent.end_char, kind))
        return spans


class ThaiNer:
    # DATE left to the regex rules, as for English (durations aren't identifiers).
    _KINDS = {"PERSON": PERSON, "LOCATION": PLACE}

    def __init__(self):
        from pythainlp.tag import NER

        self._ner = NER("thainer")

    def find(self, text: str) -> list[Span]:
        if not _THAI_CHAR_RE.search(text):
            return []
        spans = []
        pos = 0
        current_kind = None
        current_start = 0
        last_end = 0
        # tag() returns (word, IOB-tag) pairs whose words concatenate back to
        # the input, so offsets can be recovered by walking them in order.
        for word, tag in self._ner.tag(text, pos=False):
            start = text.find(word, pos)
            if start < 0:
                start = pos
            end = start + len(word)
            pos = end
            label = tag[2:] if tag[:2] in ("B-", "I-") else None
            kind = self._KINDS.get(label) if label else None
            continue_span = bool(kind) and tag.startswith("I-") and current_kind == kind
            if current_kind and not continue_span:
                spans.append(Span(current_start, last_end, current_kind))
                current_kind = None
            if kind and not continue_span:
                current_kind = kind
                current_start = start
            last_end = end
        if current_kind:
            spans.append(Span(current_start, last_end, current_kind))
        return spans


@lru_cache
def get_english_ner() -> SpacyEnglishNer:
    return SpacyEnglishNer()


@lru_cache
def get_thai_ner() -> ThaiNer:
    return ThaiNer()
