#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Every rubric's point bands are disjoint, gap-free and reach the maximum.

A score must fall in exactly one band. An overlap (``Unsatisfactory 0-8.24`` beside
``No submission 0-0``, ``Developing 2-3`` beside ``Proficient 3-4``) makes the band of a
boundary score depend on which band is read first.
"""

import hashlib
import json
from pathlib import Path

import pytest

import cqc_cpcc

pytestmark = pytest.mark.unit

CONFIG = Path(cqc_cpcc.__file__).parent / "config"
# rubric_config merges the legacy file under rubrics.json ({**legacy, **rubrics}), so a rubric that
# exists only in the legacy file (ai_assignment_reflection_rubric) is live and is checked too.
RUBRICS = {**json.loads((CONFIG / "rubrics_v1_legacy.json").read_text(encoding="utf-8")),
           **json.loads((CONFIG / "rubrics.json").read_text(encoding="utf-8"))}


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


def _digest(rubric):
    bands = {"criteria": [[c["criterion_id"], [[lv["label"], lv["score_min"], lv["score_max"]] for lv in c["levels"]]]
                          for c in rubric["criteria"] if c.get("levels") and "score_min" in c["levels"][0]],
             "overall": [[b["label"], b["score_min"], b["score_max"]] for b in rubric.get("overall_bands", [])]}
    return hashlib.sha256(json.dumps(bands, sort_keys=True).encode()).hexdigest()[:12]


# rubric_id: (rubric_version, digest of every band). The version is part of the grading run key, so a
# changed band must come with a new version, or results graded under the old bands would be reused.
# When this test fails: bump that rubric's rubric_version in rubrics.json, then update its row here.
PINNED = {
    "ai_assignment_reflection_rubric": ("1.0", "5c81d8596d81"),
    "csc113_final_reflection_rubric": ("1.1", "ce954ec9f01a"),
    "csc113_week1_reflection_rubric": ("1.0", "82efa0656ed1"),
    "csc113_week2_reflection_rubric": ("1.0", "4b08f5d173b6"),
    "csc113_week3_reflection_rubric": ("1.0", "32baada4288b"),
    "csc113_week4_reflection_rubric": ("1.1", "795128c12607"),
    "csc113_week5_reflection_rubric": ("1.1", "a43679554a31"),
    "csc113_week6_reflection_rubric": ("1.1", "5fe9a019b2b0"),
    "csc113_week7_reflection_rubric": ("1.1", "fbc1a09a2617"),
    "csc113_week8_reflection_rubric": ("1.1", "ff86bb7cf552"),
    "csc134_cpp_exam_rubric": ("3.3", "36f06fb40c83"),
    "csc134_cpp_final_exam_rubric": ("3.3", "392f56c23947"),
    "csc151_java_exam_rubric": ("3.2", "a2c7aac97aad"),
    "default_100pt_rubric": ("1.0", "64f188bf2a0f"),
}


@pytest.mark.parametrize("rubric_id", sorted(RUBRICS))
def test_changed_bands_come_with_a_new_rubric_version(rubric_id):
    rubric = RUBRICS[rubric_id]
    assert (str(rubric["rubric_version"]), _digest(rubric)) == PINNED[rubric_id], (
        f"{rubric_id}: its point bands or its rubric_version changed. Bump rubric_version in rubrics.json "
        f"(it is part of the grading run key) and update the row in PINNED.")


def test_every_rubric_is_pinned():
    assert set(PINNED) == set(RUBRICS)
