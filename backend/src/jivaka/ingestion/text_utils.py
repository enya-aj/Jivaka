import re

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_term(term: str) -> str:
    """Lowercases and collapses whitespace so entity mentions and definition
    terms can be matched despite minor surface variation."""
    return _WHITESPACE_RE.sub(" ", term.strip().lower())
