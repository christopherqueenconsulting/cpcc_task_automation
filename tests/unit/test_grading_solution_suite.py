#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""The ``grading-solution`` suite: a reference solution on every case, equivalents, bug twins."""

import collections
import json
import os
from pathlib import Path

import pytest

from cqc_cpcc.model_eval import dataset as ds
from cqc_cpcc.model_eval import dataset_builder as db
from cqc_cpcc.model_eval.suites import SUITE_MODULES, get_suite
from cqc_cpcc.model_eval.suites import grading_solution as gs
from cqc_cpcc.model_eval.suites.base import DATASETS_ROOT, SuiteCase

pytestmark = pytest.mark.unit

ROOT = DATASETS_ROOT / "grading-solution" / "v1"


@pytest.fixture(scope="module")
def cases():
    return ds.load_cases(ROOT)


def test_the_dataset_on_disk_is_what_the_builder_writes(tmp_path):
    db.write_solution_dataset(tmp_path)
    written = {p.relative_to(tmp_path): p.read_text(encoding="utf-8")
               for p in (tmp_path / "cases").rglob("*") if p.is_file()}
    on_disk = {p.relative_to(ROOT): p.read_text(encoding="utf-8")
               for p in (ROOT / "cases").rglob("*") if p.is_file()}
    assert written == on_disk
    assert (tmp_path / "VERSION").read_text() == (ROOT / "VERSION").read_text() == "v1\n"


def test_every_case_carries_its_solution_and_the_v2_cases_none(cases):
    for c in cases:
        assert c.reference_solution and db.SOLUTION_AUTHOR in c.reference_solution, c.case_id
        meta = json.loads((ROOT / "cases" / c.case_id / "case.json").read_text(encoding="utf-8"))
        assert (ROOT / "cases" / c.case_id / meta["reference_solution_file"]).read_text(
            encoding="utf-8") == c.reference_solution
        # The submission is always by another synthetic author than the solution.
        assert f"Author: {db.SOLUTION_AUTHOR}" not in "\n".join(c.files.values()), c.case_id
    assert all(c.reference_solution is None for c in ds.load_cases(ds.DEFAULT_DATASET))


@pytest.mark.parametrize("solution_file", ["../../outside.txt", "/etc/passwd", "link"])
def test_a_solution_path_outside_the_case_directory_is_refused(tmp_path, solution_file):
    outside = tmp_path / "outside.txt"
    outside.write_text("SECRET", encoding="utf-8")
    case_dir = tmp_path / "cases" / "c1"
    case_dir.mkdir(parents=True)
    (case_dir / "link").symlink_to(outside)
    meta = json.loads((ROOT / "cases" / "csc151_exam1_java__solution_format" / "case.json").read_text(encoding="utf-8"))
    files = {name: (ROOT / "cases" / meta["case_id"] / name).read_text(encoding="utf-8") for name in meta["files"]}
    with pytest.raises(ValueError, match="reference_solution_file"):
        ds._build({**meta, "reference_solution_file": solution_file}, files, case_dir)


def test_coverage_three_courses_twenty_bug_twins_and_equivalents(cases):
    by_tag = collections.Counter(t for c in cases for t in c.tags)
    assert by_tag["bug"] >= 20 and by_tag["equivalent"] >= 6 and by_tag["follows_solution_format"] == 3
    assert {c.course_id for c in cases} == {"CSC_151", "CSC_134", "CSC_251"}
    assert {c.language for c in cases} == {"java", "cpp"}
    ids = {c.case_id for c in cases}
    for c in cases:
        if "bug" in c.tags or "equivalent" in c.tags:
            assert c.twin_of in ids and c.twin_of.endswith("__solution_format"), c.case_id
        if "bug" in c.tags:
            assert c.error_ids, c.case_id
        else:
            assert not c.error_ids, c.case_id


@pytest.mark.compiler
def test_the_solution_and_every_twin_match_the_real_compilers(cases):
    from cqc_cpcc.utilities.compiler_gate import check_submission

    checked = 0
    checked_languages = set()
    required_languages = {c.language for c in cases}
    for c in cases:
        result = check_submission(list(c.files.items()))
        if not result.supported:
            continue
        assert result.compiles == c.compiles, c.case_id
        solution = check_submission([(c.files and next(iter(c.files)), c.reference_solution)])
        if not solution.supported:
            continue
        assert solution.compiles is True, c.case_id
        checked += 1
        checked_languages.add(result.language)
    strict_compilers = os.getenv("CQC_REQUIRE_REAL_COMPILERS") == "true"
    if not checked and not strict_compilers:
        pytest.skip("no dataset cases could be checked with installed compilers")
    assert checked > 0
    if strict_compilers:
        assert checked_languages == required_languages, (
            f"CI did not exercise all dataset languages: checked {sorted(checked_languages)}, "
            f"expected {sorted(required_languages)}"
        )


def test_the_builder_is_deterministic_and_every_edit_matches():
    first = [(c.case_id, c.source, c.solution) for c in db.build_solution_cases()]
    second = [(c.case_id, c.source, c.solution) for c in db.build_solution_cases()]
    assert first == second and len({cid for cid, _, _ in first}) == len(first)


def test_csc251_relabel_uses_only_its_own_error_ids():
    a = db._relabelled_for_csc251(db.ASSIGNMENTS[0])
    from cqc_cpcc.model_eval.runner import error_definitions
    known = {d.error_id for d in error_definitions("CSC_251", "Exam1")}
    for m in a.mutations:
        assert a.error_prefix + m.error_suffix in known, m.key
        for x in m.acceptable:
            assert a.error_prefix + x in known, (m.key, x)
    assert not any(m.error_suffix == "SCANNER_CLASS" for m in a.mutations)


def test_grade_once_hands_the_solution_to_the_grader(cases):
    import asyncio

    from cqc_cpcc.model_eval.runner import _grade_once, perturb

    seen = {}

    async def grade_fn(**kwargs):
        seen.update(kwargs)
        raise RuntimeError("stop here")

    case = next(c for c in cases if "bug" in c.tags)
    with pytest.raises(RuntimeError):
        asyncio.run(_grade_once(case, perturb(case, 0), grade_fn))
    assert seen["reference_solution"] == case.reference_solution


def _suite_case(eval_case):
    return SuiteCase(case_id=eval_case.case_id, stratum=eval_case.course_id, inputs={"eval_case": eval_case},
                     labels={}, tags=tuple(eval_case.tags), twin_of=eval_case.twin_of, labels_reviewed_by=None)


def test_the_graders_measure_recall_harshness_majors_and_leaks(cases):
    bug = _suite_case(next(c for c in cases if "bug" in c.tags and len(c.error_ids) == 1))
    [required] = list(bug.inputs["eval_case"].error_ids)
    assert gs._recall(bug, {"detected": [required]}) == 1.0
    assert gs._recall(bug, {"detected": ["SOMETHING_ELSE"]}) == 0.0
    assert gs._harshness(bug, {"total": 0}) is None and gs._no_major_on_equivalent(bug, {"major": []}) is None

    good = _suite_case(next(c for c in cases if "equivalent" in c.tags))
    low, _high = good.inputs["eval_case"].expected_score_range
    max_points = good.inputs["eval_case"].max_points
    assert gs._recall(good, {"detected": []}) is None
    assert gs._harshness(good, {"total": low}) == 1.0 and gs._harshness(good, {"total": max_points}) == 1.0
    assert gs._harshness(good, {"total": low - max_points / 4}) == pytest.approx(0.75)
    assert gs._no_major_on_equivalent(good, {"major": []}) == 1.0
    assert gs._no_major_on_equivalent(good, {"major": ["X"]}) == 0.0

    solution_line = next(line.strip() for line in good.inputs["eval_case"].reference_solution.splitlines()
                         if len(line.strip()) >= gs.LEAK_MIN_CHARS
                         and line.strip() not in "\n".join(good.inputs["eval_case"].files.values()))
    assert gs._no_solution_leak(good, {"texts": ["Nice work."]}) == 1.0
    assert gs._no_solution_leak(good, {"texts": ["Compare with: " + solution_line]}) == 0.0


def test_the_suite_is_registered_and_its_payload_has_what_the_graders_read():
    assert SUITE_MODULES["grading-solution"] == "cqc_cpcc.model_eval.suites.grading_solution"
    suite = get_suite("grading-solution")
    assert suite.prompt_id == "rubric-grading" and suite.dataset == "grading-solution/v1"
    assert {g.name for g in suite.graders} == {"recall_with_solution", "harshness", "no_major_on_equivalent",
                                               "no_solution_leak"}
    assert sum(suite.weights.values()) == pytest.approx(1.0)
    registry = json.loads(Path("src/cqc_cpcc/config/prompt_registry.json").read_text(encoding="utf-8"))
    assert "grading-solution" in registry["prompts"]["rubric-grading"]["suites"]
    assert suite.cases()  # loads and validates the dataset


def test_the_readme_says_the_cases_are_synthetic():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "synthetic" in text.lower() and "no case derives from a real submission" in text.lower()
