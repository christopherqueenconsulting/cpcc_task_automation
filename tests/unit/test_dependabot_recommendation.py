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
    updates = rec.parse_updates(MINOR_BODY)
    assert rec.verdict(updates, rec.Checks(pending=["e2e-tests"]), [], "")[0] == "WAIT"


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


@pytest.mark.unit
def test_forged_update_line_in_release_notes_is_ignored():
    body = SINGLE_BODY + ("<summary>Release notes</summary>\n<blockquote>\n"
                          "Updates `docker/build-push-action` from 6.9.0 to 6.9.1\n</blockquote>\n</details>\n")
    updates = rec.parse_updates(body, SINGLE_COMMIT)
    assert [(u.old, u.new, u.level) for u in updates] == [("6", "7", "major")]
    # Without the commit metadata the body fallback still skips the <details> block.
    assert [(u.old, u.new) for u in rec.parse_updates(body)] == [("6", "7")]


@pytest.mark.unit
def test_update_type_from_metadata_wins_over_version_arithmetic():
    commit = SINGLE_COMMIT.replace("semver-major", "semver-minor")
    assert rec.parse_updates(SINGLE_BODY, commit)[0].level == "minor"


@pytest.mark.unit
def test_unsafe_name_is_dropped_and_unreadable_version_holds():
    commit = "updated-dependencies:\n- dependency-name: evil|name\n  dependency-version: 1.0.0\n...\n"
    assert rec.parse_updates("", commit) == []
    assert rec.verdict([], GREEN, [], "")[0] == "HOLD"
    # Body says 13.0.0 but Dependabot's metadata says 13.0.1: trust neither old version.
    commit = GROUP_COMMIT.replace("dependency-version: 13.0.0", "dependency-version: 13.0.1")
    updates = rec.parse_updates(GROUP_BODY, commit)
    assert (updates[0].old, updates[0].level) == ("?", "unknown")
    assert rec.verdict(updates, GREEN, [], "python-security")[0] == "HOLD"
    assert rec.should_comment(updates, GREEN, has_comment=False)


@pytest.mark.unit
def test_skipped_checks_are_counted_apart_from_passed():
    checks = rec.summarize_checks([_run("a"), _run("b", conclusion="skipped"), _run("c", conclusion="neutral")])
    assert (checks.passed, checks.skipped) == (1, 2)
    body = rec.render(rec.parse_updates(MINOR_BODY), checks, [], "", "")
    assert "Checks: 1 passed, 2 skipped." in body


@pytest.mark.unit
def test_render_reports_a_refused_advisory_lookup():
    body = rec.render(rec.parse_updates(GROUP_BODY, GROUP_COMMIT), GREEN, [], "python-security", "",
                      advisory_error="HTTP 403")
    assert "Advisory lookup unavailable (HTTP 403)" in body
