"""Shared helper for producing a short (3-sentence) job-abstract from a description.

Used by all three source modules so every search listing carries a concise
summary of the role without dumping the full description.
"""

import re
from typing import List


def _split_sentences(text: str) -> List[str]:
    """Split text into sentences on . ? ! boundaries (heuristic, German-friendly)."""
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    parts = re.split(r"(?<=[.?!])\s+(?=[A-Z\u00C4\u00D6\u00DCa-z\u00E4\u00F6\u00FC0-9\"'(])", text)
    return [p.strip() for p in parts if p.strip()]


def make_abstract(text, sentences: int = 3, max_chars: int = 360) -> str:
    """Return a trimmed, 3-sentence abstract from a raw description.

    Returns an empty string when there is no usable text. Falls back to a
    short truncation when the text has no sentence structure.
    """
    if not text:
        return ""
    clean = re.sub(r"\s+", " ", str(text)).strip()
    # Drop leading bullets / numbering / punctuation noise.
    clean = re.sub(r"^[\s\-\u2022*#.\d)]+", "", clean).strip()
    if not clean:
        return ""
    sents = _split_sentences(clean)
    picked = sents[:sentences] if sents else [clean]
    abstract = " ".join(picked).strip()
    if len(abstract) > max_chars:
        abstract = abstract[:max_chars].rstrip()
    if abstract and not abstract.endswith((".", "!", "?", "\u2026")):
        abstract += "\u2026"
    return abstract.strip()
