#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Small deterministic helpers shared by the suites' code graders."""

from __future__ import annotations

import re
from typing import Iterable, Optional


def set_f1(predicted: Iterable, expected: Iterable, acceptable: Iterable = ()) -> float:
    """F1 of two label sets; ``acceptable`` extras are not false positives. Both empty = 1."""
    predicted, expected, acceptable = set(predicted), set(expected), set(acceptable)
    tp = len(predicted & expected)
    fp = len(predicted - expected - acceptable)
    fn = len(expected - predicted)
    if tp == fp == fn == 0:
        return 1.0
    return 2 * tp / (2 * tp + fp + fn)


def invalid_share(predicted: Iterable, allowed: Iterable) -> float:
    """1.0 when every predicted label is allowed, else the share that is."""
    predicted = list(predicted)
    if not predicted:
        return 1.0
    allowed = set(allowed)
    return sum(1 for p in predicted if p in allowed) / len(predicted)


def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _stem(word: str) -> str:
    """Tiny English stemmer: plural and -ing/-ed endings ("scores" -> "score", "guesses" -> "guess")."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith(("sses", "shes", "ches", "xes", "zes")):
        return word[:-2]
    if word.endswith("s") and not word.endswith(("ss", "us", "is")) and len(word) > 3:
        return word[:-1]
    for suffix in ("ing", "ed"):
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def stemmed_words(text: str) -> set:
    return {_stem(w) for w in normalize(text).split()}


def matches_keyword_groups(text: str, groups: list) -> bool:
    """True when every group has at least one keyword (a phrase) present in ``text``.

    Matching is case- and punctuation-insensitive with simple suffix stemming, so
    "computes the totals" matches the keyword "total".
    """
    words = stemmed_words(text)
    flat = " " + " ".join(sorted(words)) + " "
    norm = " " + normalize(text) + " "
    for group in groups:
        hit = False
        for keyword in group:
            kw_words = [_stem(w) for w in normalize(keyword).split()]
            if len(kw_words) == 1 and kw_words[0] in words:
                hit = True
            elif len(kw_words) > 1 and (" " + normalize(keyword) + " ") in norm:
                hit = True
            elif kw_words and all(w in words for w in kw_words):
                hit = True
            if hit:
                break
        if not hit:
            return False
    return True if groups else bool(flat.strip())


CODE_BLOCK = re.compile(r"```.*?```", re.S)


def longest_code_block_lines(text: str) -> int:
    blocks = CODE_BLOCK.findall(text or "")
    return max((b.count("\n") - 1 for b in blocks), default=0)


def no_solution_leak(text: str, max_lines: int = 8) -> float:
    """1.0 unless the text contains a code block longer than ``max_lines`` lines."""
    return 0.0 if longest_code_block_lines(text) > max_lines else 1.0


def length_within(text: str, max_chars: int, min_chars: int = 1) -> Optional[float]:
    length = len((text or "").strip())
    if length < min_chars:
        return 0.0
    return 1.0 if length <= max_chars else max(0.0, 1.0 - (length - max_chars) / max_chars)


def range_accuracy(value: Optional[float], low: float, high: float, scale: float) -> float:
    """1 inside [low, high], falling linearly to 0 at ``scale`` away."""
    if value is None:
        return 0.0
    distance = max(low - value, value - high, 0.0)
    return max(0.0, 1.0 - distance / scale) if scale else 0.0
