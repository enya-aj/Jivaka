import re
from functools import lru_cache

from jivaka.ingestion.text_utils import normalize_term

THAI_CHAR_RE = re.compile("[฀-๿]")
PSEUDONYM_RE = re.compile(r"\bP-\d{4,}\b", re.IGNORECASE)

_MAX_NGRAM = 4
_LATIN_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9'\-]*")
_WORD_CHAR_RE = re.compile(r"[a-z0-9฀-๿]")

_ENGLISH_STOPWORDS = frozenset(
    """a an and are as at be been but by can could did do does for from had has have how i if in
    into is it its may me my no not of on or our should so than that the their them then there
    these they this those to was we were what when where which who why will with would you your
    about any also tell explain describe give list show patient patients""".split()
)


@lru_cache
def _thai_stopwords() -> frozenset:
    from pythainlp.corpus import thai_stopwords

    return frozenset(thai_stopwords())


def has_thai(text: str) -> bool:
    return bool(THAI_CHAR_RE.search(text))


def find_pseudonyms(text: str) -> list[str]:
    return sorted({m.group(0).upper() for m in PSEUDONYM_RE.finditer(text)})


def _tokens(text: str) -> list[str]:
    if has_thai(text):
        from pythainlp.tokenize import word_tokenize

        return [t for t in word_tokenize(text, engine="newmm", keep_whitespace=False) if _WORD_CHAR_RE.search(t)]
    return _LATIN_TOKEN_RE.findall(text)


def extract_terms(question: str) -> list[str]:
    """Candidate entity names in a question: every 1-4 word run, normalised
    the same way ingestion normalises entity names, so they can be matched
    against Entity.normalized_name / Definition.normalized_term by index.
    Thai runs are also tried without spaces, since Thai entity names are
    written without them."""
    text = PSEUDONYM_RE.sub(" ", question.lower())
    tokens = _tokens(text)
    stopwords = _ENGLISH_STOPWORDS | (_thai_stopwords() if has_thai(text) else frozenset())
    terms: dict[str, None] = {}
    for n in range(1, _MAX_NGRAM + 1):
        for i in range(len(tokens) - n + 1):
            window = tokens[i : i + n]
            if window[0] in stopwords or window[-1] in stopwords:
                continue
            if n == 1 and len(window[0]) < (2 if has_thai(window[0]) else 3):
                continue
            terms[normalize_term(" ".join(window))] = None
            if any(has_thai(t) for t in window):
                terms[normalize_term("".join(window))] = None
    return list(terms)
