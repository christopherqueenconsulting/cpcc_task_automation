#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""scripts/dependabot_recommendation.py: parse a Dependabot PR and give a fixed-rule verdict."""

import importlib.util
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "dependabot_recommendation",
    Path(__file__).resolve().parents[2] / "scripts" / "dependabot_recommendation.py")
rec = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = rec  # dataclasses resolves annotations through sys.modules
_SPEC.loader.exec_module(rec)

# Shapes copied from real PRs: #555 (security group), #540 (minor group), #537 (single bump).
GROUP_BODY = (
    "Bumps the python-security group with 1 update in the / directory: "
    "[pillow](https://github.com/python-pillow/Pillow).\n\n\n"
    "Updates `pillow` from 12.3.0 to 13.0.0\n- [Release notes](https://example.invalid)\n")
GROUP_COMMIT = (
    "chore(deps): bump pillow\n\n---\nupdated-dependencies:\n"
    "- dependency-name: pillow\n  dependency-version: 13.0.0\n  dependency-type: direct:production\n"
    "  dependency-group: python-security\n...\n\nSigned-off-by: dependabot[bot] <support@github.com>\n")
MINOR_BODY = "Updates `selenium` from 4.51.0 to 4.52.0\nUpdates `pandas` from 3.0.6 to 3.1.1\n"
SINGLE_BODY = ("Bumps [docker/build-push-action](https://github.com/docker/build-push-action) "
               "from 6 to 7.\n<details>\n")
SINGLE_COMMIT = ("updated-dependencies:\n- dependency-name: docker/build-push-action\n"
                 "  dependency-version: '7'\n  dependency-type: direct:production\n"
                 "  update-type: version-update:semver-major\n...\n")

GREEN = rec.Checks(passed=8)


def _run(name, status="completed", conclusion="success", started="2026-11-11T06:00:00Z"):
    return {"name": name, "status": status, "conclusion": conclusion, "started_at": started}


@pytest.mark.unit
@pytest.mark.parametrize("old, new, level", [
    ("12.3.0", "13.0.0", "major"),
    ("6", "7", "major"),
    ("3.0.6", "3.1.1", "minor"),
    ("4.51.0", "4.52.0", "minor"),
    ("1.3.70", "1.3.71", "patch"),
    ("v9.0.0", "v10.3.3", "major"),
])
def test_semver_level(old, new, level):
    assert rec.semver_level(old, new) == level


@pytest.mark.unit
def test_parse_group_body_with_commit_metadata():
    updates = rec.parse_updates(GROUP_BODY, GROUP_COMMIT)
    assert [(u.name, u.old, u.new, u.level, u.dep_type) for u in updates] == [
        ("pillow", "12.3.0", "13.0.0", "major", "direct:production")]
    assert rec.dependency_group(GROUP_COMMIT) == "python-security"


@pytest.mark.unit
def test_parse_single_bump_strips_trailing_period():
    updates = rec.parse_updates(SINGLE_BODY, SINGLE_COMMIT)
    assert [(u.name, u.old, u.new, u.level) for u in updates] == [
        ("docker/build-push-action", "6", "7", "major")]


@pytest.mark.unit
def test_parse_without_metadata_marks_type_unknown():
    updates = rec.parse_updates(MINOR_BODY)
    assert [u.name for u in updates] == ["selenium", "pandas"]
    assert {u.dep_type for u in updates} == {"unknown"}


@pytest.mark.unit
def test_summarize_checks_keeps_newest_rerun_and_ignores_cancelled_and_self():
    runs = [
        _run("unit-tests", conclusion="failure", started="2026-11-11T06:00:00Z"),
        _run("unit-tests", conclusion="success", started="2026-11-11T07:00:00Z"),
        _run("e2e-tests", conclusion="cancelled"),
        _run("Analyze (python)", status="in_progress", conclusion=None),
        _run("PII guard", conclusion="failure"),
        _run("recommend", status="in_progress", conclusion=None),
    ]
    checks = rec.summarize_checks(runs, ignore="recommend")
    assert checks.failed == ["PII guard"]
    assert checks.pending == ["Analyze (python)"]
    assert checks.passed == 2


@pytest.mark.unit
def test_failed_check_holds_even_a_security_fix():
    updates = rec.parse_updates(GROUP_BODY, GROUP_COMMIT)
    word, reason = rec.verdict(updates, rec.Checks(failed=["unit-tests"]), [], "python-security")
    assert word == "HOLD" and "unit-tests" in reason and "agent-ready" in reason


@pytest.mark.unit
def test_pending_checks_wait():
    assert rec.verdict([], rec.Checks(pending=["e2e-tests"]), [], "")[0] == "WAIT"


@pytest.mark.unit
def test_major_security_fix_merges_and_names_worst_severity():
    updates = rec.parse_updates(GROUP_BODY, GROUP_COMMIT)
    advisories = [rec.Advisory("pillow", "GHSA-1", "high", "x"), rec.Advisory("pillow", "GHSA-2", "critical", "y")]
    word, reason = rec.verdict(updates, GREEN, advisories, "python-security")
    assert word == "MERGE" and "2 open security advisories" in reason and "critical" in reason


@pytest.mark.unit
def test_major_runtime_bump_without_advisory_needs_smoke():
    updates = rec.parse_updates(SINGLE_BODY, SINGLE_COMMIT)
    assert rec.verdict(updates, GREEN, [], "")[0] == "MERGE AFTER SMOKE"


@pytest.mark.unit
def test_major_dev_only_bump_merges():
    updates = [rec.Update("syrupy", "5.5.3", "6.1.1", "direct:development")]
    assert rec.verdict(updates, GREEN, [], "")[0] == "MERGE"


@pytest.mark.unit
def test_comment_only_for_major_red_or_existing():
    minor = rec.parse_updates(MINOR_BODY)
    major = rec.parse_updates(GROUP_BODY, GROUP_COMMIT)
    assert not rec.should_comment(minor, GREEN, has_comment=False)
    assert rec.should_comment(minor, rec.Checks(failed=["unit-tests"]), has_comment=False)
    assert rec.should_comment(minor, GREEN, has_comment=True)
    assert rec.should_comment(major, GREEN, has_comment=False)


@pytest.mark.unit
def test_render_has_marker_verdict_and_table():
    updates = rec.parse_updates(GROUP_BODY, GROUP_COMMIT)
    body = rec.render(updates, GREEN, [rec.Advisory("pillow", "GHSA-5x8w", "critical", "overflow")],
                      "python-security", "https://example.invalid/run")
    assert body.startswith(rec.MARKER)
    assert "**MERGE**" in body
    assert "| `pillow` | 12.3.0 | 13.0.0 | major | direct:production |" in body
    assert "critical GHSA-5x8w `pillow`" in body


@pytest.mark.unit
def test_render_lists_worst_advisories_first_and_counts_the_rest():
    updates = rec.parse_updates(GROUP_BODY, GROUP_COMMIT)
    advisories = [rec.Advisory("pillow", f"GHSA-{i}", "medium", "m") for i in range(12)]
    advisories.append(rec.Advisory("pillow", "GHSA-crit", "critical", " c "))
    body = rec.render(updates, GREEN, advisories, "", "")
    listed = [line for line in body.splitlines() if line.startswith("- ")]
    assert listed[0] == "- critical GHSA-crit `pillow`: c"
    assert listed[-1] == "- and 3 more (Security tab, Dependabot alerts)"
