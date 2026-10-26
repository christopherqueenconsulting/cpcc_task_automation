"""Turn the grader's internal ids back into words before anything reaches the student.

The grading prompt names requirements (``R1``), output-check test cases (``T1``, ``M1``, ``S1``)
and selection/loop conditions (``C1``) by short ids, which keeps the prompt small. The model
copies those ids into its feedback ("T1/T3 outputs differ", "R4 is incomplete"), but the student
never sees the checklist or the test plan, so an id means nothing to them.

``humanize_result_ids`` rewrites every student-facing text field of a graded result after the
model call, deterministically: a test case becomes its label, a requirement or condition becomes
its text. Only ids that exist in this run's checklist or plan are replaced, and text inside
backticks (quoted code) is left alone, so a student's own variable named ``T1`` in a code quote
is not touched. No LLM call, no prompt change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

#: Requirement and condition text longer than this is cut at a word boundary with an ellipsis.
MAX_QUOTED_CHARS = 80

KIND_TEST = "test"
KIND_REQUIREMENT = "requirement"
KIND_CONDITION = "condition"

# A label the test-case generator falls back to ("Case 3") says nothing to the student.
_GENERIC_LABEL_RE = re.compile(r"^\s*(?:test\s*case|case|test|run)?\s*#?\d*\s*$", re.IGNORECASE)
# The noun a writer may put before an id ("case T1", "requirements R2 and R3"); it is absorbed so
# the replacement does not read "case the "Typical order" test".
_LEAD_NOUN = r"(?:(?:test\s+cases?|cases?|tests?|runs?|inputs?|requirements?|reqs?\.?|conditions?)\s+)?"
_SEPARATOR = r"\s*(?:/|,|&|\band\b|\bor\b|-|–)\s*"
_RANGE_SEPARATORS = ("-", "–")
_CODE_SPAN_RE = re.compile(r"(`+)(.+?)\1", re.DOTALL)


@dataclass(frozen=True)
class IdEntry:
    """What one internal id stands for."""

    kind: str
    text: str  # the case label, or the requirement / condition text; "" when there is nothing useful


def _quote(text: str) -> str:
    text = " ".join(str(text or "").split())
    if len(text) > MAX_QUOTED_CHARS:
        text = text[:MAX_QUOTED_CHARS].rsplit(" ", 1)[0].rstrip(",;:.") + "…"
    return f"“{text}”"


def build_id_glossary(requirements=None, execution_plan=None) -> dict:
    """``{id: IdEntry}`` for the requirements, test cases and conditions this run used."""
    glossary: dict = {}
    for item in getattr(requirements, "requirements", None) or []:
        if getattr(item, "id", None):
            glossary[str(item.id)] = IdEntry(KIND_REQUIREMENT, str(getattr(item, "text", "") or ""))
    for case in getattr(execution_plan, "cases", None) or []:
        if getattr(case, "id", None):
            label = str(getattr(case, "label", "") or "")
            glossary[str(case.id)] = IdEntry(KIND_TEST, "" if _GENERIC_LABEL_RE.match(label) else label)
    for cond in getattr(execution_plan, "conditions", None) or []:
        if getattr(cond, "id", None):
            glossary[str(cond.id)] = IdEntry(KIND_CONDITION, str(getattr(cond, "text", "") or ""))
    # A one-character id ("M") would match too much ordinary text.
    return {k: v for k, v in glossary.items() if len(k) >= 2}


def _join(items: list) -> str:
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def _phrase(entries: list) -> str:
    """Words for one or more ids of the same kind."""
    kind = entries[0].kind
    if kind == KIND_TEST:
        labels = [_quote(e.text) for e in entries if e.text]
        generic = len(entries) - len(labels)
        if not labels:
            return "one of the test runs" if generic == 1 else "some of the test runs"
        noun = "test" if len(labels) == 1 else "tests"
        out = f"the {_join(labels)} {noun}"
        if generic:
            out += " and " + ("one other test run" if generic == 1 else f"{generic} other test runs")
        return out
    noun = "requirement" if kind == KIND_REQUIREMENT else "condition"
    texts = [_quote(e.text) for e in entries if e.text]
    if not texts:
        return f"one of the {noun}s" if len(entries) == 1 else f"some of the {noun}s"
    return f"the {noun}{'s' if len(texts) > 1 else ''} {_join(texts)}"


def _expand_range(first: str, last: str, glossary: dict) -> Optional[list]:
    """``T1``..``T3`` -> ``[T1, T2, T3]`` when both ends share a prefix and every id exists."""
    a, b = re.fullmatch(r"([A-Za-z]+)(\d+)", first), re.fullmatch(r"([A-Za-z]+)(\d+)", last)
    if not a or not b or a.group(1) != b.group(1) or int(a.group(2)) >= int(b.group(2)):
        return None
    ids = [f"{a.group(1)}{n}" for n in range(int(a.group(2)), int(b.group(2)) + 1)]
    return ids if all(i in glossary for i in ids) else None


def _rewrite_plain(text: str, glossary: dict, run_re: re.Pattern, id_re: re.Pattern) -> str:
    def replace(match: re.Match) -> str:
        body = match.group("run")
        ids = id_re.findall(body)
        seps = re.findall(_SEPARATOR, body)
        expanded: list = []
        for i, current in enumerate(ids):
            if i and seps[i - 1].strip() in _RANGE_SEPARATORS:
                span = _expand_range(ids[i - 1], current, glossary)
                if span:
                    expanded.extend(span[1:])
                    continue
            expanded.append(current)
        entries = [glossary[i] for i in dict.fromkeys(expanded)]
        if len({e.kind for e in entries}) == 1:
            return _phrase(entries)
        return _join([_phrase([e]) for e in entries])

    return run_re.sub(replace, text)


def humanize_ids(text: Optional[str], glossary: dict) -> Optional[str]:
    """Replace this run's ids in ``text`` with words; quoted code (backticks) is left as is."""
    if not text or not glossary:
        return text
    alternatives = "|".join(re.escape(k) for k in sorted(glossary, key=len, reverse=True))
    one = rf"(?<![A-Za-z0-9_])(?:{alternatives})(?![A-Za-z0-9_])"
    id_re = re.compile(one)
    run_re = re.compile(rf"(?i:{_LEAD_NOUN})(?P<run>{one}(?:{_SEPARATOR}{one})*)")
    out, last = [], 0
    for code in _CODE_SPAN_RE.finditer(text):
        out.append(_rewrite_plain(text[last:code.start()], glossary, run_re, id_re))
        out.append(code.group(0))
        last = code.end()
    out.append(_rewrite_plain(text[last:], glossary, run_re, id_re))
    return "".join(out)


def _error(err, glossary: dict):
    if err is None:
        return None
    return err.model_copy(update={
        "description": humanize_ids(err.description, glossary),
        "notes": humanize_ids(err.notes, glossary),
    })


def humanize_result_ids(result, requirements=None, execution_plan=None):
    """A copy of ``result`` whose student-facing text names things instead of ids.

    Covers criterion feedback and evidence, the overall feedback and every detected error,
    including errors a reconciliation stage set aside (Reinstate puts them back in front of
    the student). Instructor-only fields (reconciliation reasons, the checklist evidence)
    keep the ids: the instructor sees them next to the checklist and the test plan.
    """
    glossary = build_id_glossary(requirements, execution_plan)
    if not glossary:
        return result
    criteria = [c.model_copy(update={
        "feedback": humanize_ids(c.feedback, glossary),
        "evidence": [humanize_ids(e, glossary) for e in c.evidence] if c.evidence else c.evidence,
    }) for c in result.criteria_results]
    errors = [_error(e, glossary) for e in result.detected_errors] if result.detected_errors else result.detected_errors
    notes = [n.model_copy(update={"error": _error(n.error, glossary)}) if n.error is not None else n
             for n in (result.reconciliation or [])]
    return result.model_copy(update={
        "criteria_results": criteria,
        "overall_feedback": humanize_ids(result.overall_feedback, glossary),
        "detected_errors": errors,
        "reconciliation": notes,
    })
