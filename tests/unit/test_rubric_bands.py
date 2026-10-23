#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Every rubric's point bands are disjoint, gap-free and reach the maximum.

A score must fall in exactly one band. An overlap (``Unsatisfactory 0-8.24`` beside
``No submission 0-0``, ``Developing 2-3`` beside ``Proficient 3-4``) makes the band of a
boundary score depend on which band is read first.
"""

import json
from pathlib import Path

import pytest

import cqc_cpcc

pytestmark = pytest.mark.unit

RUBRICS = json.loads((Path(cqc_cpcc.__file__).parent / "config" / "rubrics.json").read_text(encoding="utf-8"))


def _band_sets():
    for rubric_id, rubric in RUBRICS.items():
        for criterion in rubric["criteria"]:
            levels = criterion.get("levels") or []
            if levels and "score_min" in levels[0]:
                yield f"{rubric_id}:{criterion['criterion_id']}", levels, criterion.get("max_points")
        if rubric.get("overall_bands"):
            yield f"{rubric_id}:overall", rubric["overall_bands"], sum(c["max_points"] for c in rubric["criteria"])


BAND_SETS = list(_band_sets())
IDS = [name for name, _, _ in BAND_SETS]


def _step(*values):
    """0.01 when any boundary has two decimals (the 27.01 / 24.99 convention), else 1."""
    return 0.01 if any(round(v * 100) % 100 for v in values) else 1


@pytest.mark.parametrize("name,bands,top", BAND_SETS, ids=IDS)
def test_bands_do_not_overlap_or_leave_gaps(name, bands, top):
    ordered = sorted(bands, key=lambda b: (b["score_min"], b["score_max"]))
    for band in ordered:
        assert band["score_min"] <= band["score_max"], (name, band["label"])
    for below, above in zip(ordered, ordered[1:]):
        gap = round(above["score_min"] - below["score_max"], 4)
        step = _step(below["score_max"], above["score_min"])
        assert gap > 0, f"{name}: {below['label']} overlaps {above['label']}"
        assert gap <= step + 1e-9, f"{name}: gap of {gap} between {below['label']} and {above['label']}"


@pytest.mark.parametrize("name,bands,top", BAND_SETS, ids=IDS)
def test_the_highest_band_reaches_the_maximum(name, bands, top):
    assert max(b["score_max"] for b in bands) == top, name
