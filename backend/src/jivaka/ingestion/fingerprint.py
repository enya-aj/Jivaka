"""Content fingerprints, for spotting the same book uploaded under another
name (exact) or as another scan or edition (near).

Both work on the extracted text, not the file bytes:
- content_hash: SHA-256 of the text with case, spacing and punctuation
  removed, so a re-saved or renamed copy of the same text matches exactly.
- signature: a bottom-k MinHash sketch of 5-word shingles. Comparing two
  sketches estimates how much of the text the documents share (Jaccard
  similarity), which stays high across OCR noise, small edits and
  re-formatting.
"""

import hashlib
import unicodedata
import zlib
from array import array
from dataclasses import dataclass
from typing import Sequence

SHINGLE_WORDS = 5
SIGNATURE_SIZE = 256
# Thai (and other unspaced scripts) run whole sentences together; split long
# runs into pieces this long so they still make several shingles.
MAX_TOKEN_CHARS = 8
# Documents at least this similar get a warning before ingesting.
NEAR_DUPLICATE_THRESHOLD = 0.9
# Texts shorter than this many words are too short for a meaningful
# similarity estimate; only the exact check applies to them.
MIN_WORDS_FOR_SIGNATURE = 50


@dataclass
class Fingerprint:
    content_hash: str
    signature: list[int]


def _keep(ch: str) -> bool:
    # Letters, combining marks (Thai vowels and tone marks) and digits.
    return unicodedata.category(ch)[0] in "LMN"


def normalized_tokens(text: str) -> list[str]:
    text = unicodedata.normalize("NFKC", text).casefold()
    tokens: list[str] = []
    for raw in text.split():
        word = "".join(ch for ch in raw if _keep(ch))
        if not word:
            continue
        if len(word) <= MAX_TOKEN_CHARS:
            tokens.append(word)
        else:
            tokens.extend(word[i : i + MAX_TOKEN_CHARS] for i in range(0, len(word), MAX_TOKEN_CHARS))
    return tokens


def fingerprint(text: str) -> Fingerprint:
    tokens = normalized_tokens(text)
    content_hash = hashlib.sha256("".join(tokens).encode("utf-8")).hexdigest()
    if len(tokens) < MIN_WORDS_FOR_SIGNATURE:
        return Fingerprint(content_hash=content_hash, signature=[])
    hashes = array("I")
    for i in range(len(tokens) - SHINGLE_WORDS + 1):
        hashes.append(zlib.crc32(" ".join(tokens[i : i + SHINGLE_WORDS]).encode("utf-8")))
    return Fingerprint(content_hash=content_hash, signature=_bottom_k(hashes, SIGNATURE_SIZE))


def _bottom_k(hashes: array, k: int) -> list[int]:
    try:
        import numpy as np
    except ImportError:  # pragma: no cover - numpy ships with the OCR stack
        return sorted(set(hashes))[:k]
    return [int(h) for h in np.unique(np.frombuffer(hashes, dtype=np.uint32))[:k]]


def similarity(a: Sequence[int], b: Sequence[int]) -> float:
    """Estimated Jaccard similarity of the two texts' shingle sets: the
    share of the union's k smallest hashes that both sketches hold."""
    if not a or not b:
        return 0.0
    k = min(len(a), len(b), SIGNATURE_SIZE)
    set_a, set_b = set(a), set(b)
    union_smallest = sorted(set_a | set_b)[:k]
    both = sum(1 for h in union_smallest if h in set_a and h in set_b)
    return both / k
