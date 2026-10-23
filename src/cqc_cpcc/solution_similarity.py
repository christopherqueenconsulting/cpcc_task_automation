#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""How close a submission's structure is to the reference solution (instructor-only).

The flag is information for the instructor, never a score input and never called a
"leak": a student who follows the solution's shape closely has done nothing wrong by this
measure. Identifiers and literals are normalised before comparing, so renaming variables or
changing a constant does not hide the shape, and comments and blank lines are ignored.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

#: Ratio at or above which the drawer and the results table show the flag. Measured on the
#: grading-solution dataset (54 cases, 2026-10-23): the solution by another author and the
#: rename-only or reorder-only rewrites score 1.0; the structural rewrites (a helper method,
#: a ternary, ``String.format`` for ``printf``, a ``do``/``while``) 0.88 to 0.96; the bug
#: twins 0.88 to 1.0. The threshold sits just above the structural rewrites.
DEFAULT_THRESHOLD = 0.96

_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/|#[^\n]*", re.S)
_STRING = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'')
_NUMBER = re.compile(r"\b\d+(?:\.\d+)?\b")
_TOKEN = re.compile(r"[A-Za-z_]\w*|\d+(?:\.\d+)?|\S")

#: Words kept as themselves: the languages' keywords and the library names students use.
_KEYWORDS = frozenset("""
abstract boolean break byte case catch char class const continue default do double else enum
extends final finally float for goto if implements import instanceof int interface long native
new package private protected public return short static strictfp super switch synchronized
this throw throws transient try void volatile while true false null
auto bool cin cout endl fixed include namespace setprecision signed sizeof std string struct
typedef union unsigned using virtual
and as assert async await def del elif except from global in is lambda nonlocal not or pass
raise with yield print input len range str float int list dict
System out in println print printf Scanner String Math nextInt nextDouble nextLine close
format main args
""".split())


def normalize(source: str) -> list[str]:
    """The token shape of ``source``: comments gone, strings ``S``, numbers ``N``, identifiers
    that are not keywords or library names ``V``."""
    text = _COMMENT.sub(" ", source or "")
    text = _STRING.sub(' "S" ', text)
    text = _NUMBER.sub(" N ", text)
    out = []
    for token in _TOKEN.findall(text):
        if token in _KEYWORDS or token in ("S", "N") or not token[0].isalpha() and token[0] != "_":
            out.append(token)
        else:
            out.append("V")
    return out


@dataclass(frozen=True)
class Similarity:
    ratio: float
    flagged: bool
    threshold: float


def solution_similarity(submission: str, solution: str, threshold: float = DEFAULT_THRESHOLD) -> Similarity:
    """``difflib`` ratio of the two token shapes, and whether it reaches ``threshold``."""
    a, b = normalize(submission), normalize(solution)
    if not a or not b:
        return Similarity(0.0, False, threshold)
    ratio = difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()
    return Similarity(round(ratio, 4), ratio >= threshold, threshold)


def similarity_label(sim: Similarity | None) -> str:
    """The results-table cell: ``"very similar (0.98)"`` or ``""``."""
    if sim is None or not sim.flagged:
        return ""
    return f"very similar ({sim.ratio:.2f})"
