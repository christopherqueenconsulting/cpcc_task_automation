#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Models and solution loading for the output check (PR 13). No Docker, no model."""

import io
import zipfile

import pytest

from cqc_cpcc import execution_check as ec

pytestmark = pytest.mark.unit

JAVA = "public class Main { public static void main(String[] a) { System.out.println(1); } }"


def _plan(**kw):
    base = dict(
        language="java", solution_sha256="s1",
        cases=[ec.TestCase(id="T1", stdin="5\n", source="llm", boundary_of=["C1"],
                           expected_output="x\n", validated=True)],
        conditions=[ec.Condition(id="C1", text="over 40")],
        condition_map={"C1": ["T1"]},
    )
    base.update(kw)
    return ec.ExecutionPlan(**base)


class TestPlanHash:
    def test_stable_for_equal_plans(self):
        assert _plan().plan_hash == _plan().plan_hash

    @pytest.mark.parametrize("change", [
        {"solution_sha256": "s2"},
        {"language": "cpp"},
        {"ignore_patterns": ["^Debug"]},
        {"condition_map": {"C1": []}},
        {"enabled": False},
        {"cases": [ec.TestCase(id="T1", stdin="6\n", source="llm", boundary_of=["C1"],
                               expected_output="x\n", validated=True)]},
        {"cases": [ec.TestCase(id="T1", stdin="5\n", source="llm", boundary_of=["C1"],
                               expected_output="y\n", validated=True)]},
    ])
    def test_changes_when_a_verdict_could_change(self, change):
        assert _plan(**change).plan_hash != _plan().plan_hash

    def test_labels_and_warnings_do_not_change_it(self):
        other = _plan(warnings=["The solution uses random numbers."])
        other.cases[0].label = "renamed"
        assert other.plan_hash == _plan().plan_hash

    def test_solution_text_is_never_in_the_plan(self):
        sources = ec.SolutionSources("java", (ec.SourceFile("Main.java", "SECRET-SOLUTION-LINE"),))
        plan = _plan(solution_files=sources.solution_files())
        assert "SECRET-SOLUTION-LINE" not in plan.model_dump_json()


class TestCoverage:
    def test_uncovered_when_the_boundary_case_is_not_validated(self):
        plan = _plan()
        assert plan.uncovered_conditions() == []
        plan.cases[0].validated = False
        assert plan.uncovered_conditions() == ["C1"]

    def test_uncovered_when_the_map_is_empty_even_if_cases_exist(self):
        assert _plan(condition_map={"C1": []}).uncovered_conditions() == ["C1"]

    def test_disabled_case_is_not_usable(self):
        plan = _plan()
        plan.cases[0].enabled = False
        assert plan.usable_cases() == []
        assert plan.uncovered_conditions() == ["C1"]


def _zip(entries: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


class TestLoadSolutionSources:
    def test_single_file_bytes(self):
        s = ec.load_solution_sources(JAVA.encode(), "Main.java")
        assert s.language == "java" and [f.name for f in s.files] == ["Main.java"]

    def test_single_file_path(self, tmp_path):
        path = tmp_path / "prog.py"
        path.write_text("print(1)\r\n")
        s = ec.load_solution_sources(path)
        assert s.language == "python" and s.files[0].content == "print(1)\n"

    def test_directory(self, tmp_path):
        (tmp_path / "A.java").write_text("class A {}")
        (tmp_path / "notes.txt").write_text("x")
        s = ec.load_solution_sources(tmp_path)
        assert [f.name for f in s.files] == ["A.java"]

    def test_zip_with_folders_and_noise(self):
        data = _zip({
            "sol/Main.java": JAVA, "sol/Helper.java": "class Helper {}",
            "__MACOSX/sol/._Main.java": "junk", "sol/readme.txt": "text", "sol/": "",
        })
        s = ec.load_solution_sources(data, "solution.zip")
        assert sorted(f.name for f in s.files) == ["Helper.java", "Main.java"]
        assert s.language == "java"

    def test_zip_path_traversal_keeps_only_the_base_name(self):
        s = ec.load_solution_sources(_zip({"../../etc/Evil.java": "class Evil {}"}), "s.zip")
        assert [f.name for f in s.files] == ["Evil.java"]

    def test_zip_duplicate_base_names_are_refused(self):
        with pytest.raises(ec.SolutionLoadError, match="share a name"):
            ec.load_solution_sources(_zip({"a/Main.java": "class A {}", "b/Main.java": "class B {}"}), "s.zip")

    def test_header_does_not_decide_the_language(self):
        s = ec.load_solution_sources(_zip({"main.cpp": "int main(){}", "util.h": "#pragma once"}), "s.zip")
        assert s.language == "cpp"

    def test_mixed_languages_refused(self):
        with pytest.raises(ec.SolutionLoadError, match="mixes"):
            ec.load_solution_sources(_zip({"A.java": "class A {}", "b.py": "print(1)"}), "s.zip")

    def test_nothing_usable_refused(self):
        with pytest.raises(ec.SolutionLoadError, match="no source file"):
            ec.load_solution_sources(_zip({"notes.txt": "x"}), "s.zip")

    def test_unsupported_single_file(self):
        with pytest.raises(ec.SolutionLoadError, match="unsupported"):
            ec.load_solution_sources(b"x", "notes.txt")

    def test_not_a_zip(self):
        with pytest.raises(ec.SolutionLoadError, match="not readable"):
            ec.load_solution_sources(b"not a zip", "s.zip")

    def test_declared_size_checked_before_reading(self):
        big = "x" * (ec.MAX_SOLUTION_FILE_BYTES + 1)
        with pytest.raises(ec.SolutionLoadError, match="too large"):
            ec.load_solution_sources(_zip({"Big.java": big}), "s.zip")
        with pytest.raises(ec.SolutionLoadError, match="too large"):
            ec.load_solution_sources(big.encode(), "Big.java")

    def test_total_size_cap(self):
        chunk = "x" * (200 * 1024)
        entries = {f"F{i}.java": chunk for i in range(4)}
        with pytest.raises(ec.SolutionLoadError, match="too large"):
            ec.load_solution_sources(_zip(entries), "s.zip")

    def test_too_many_entries(self):
        entries = {f"d{i}/n.txt": "x" for i in range(ec.MAX_ZIP_ENTRIES + 1)}
        with pytest.raises(ec.SolutionLoadError, match="too many"):
            ec.load_solution_sources(_zip(entries), "s.zip")

    @pytest.mark.parametrize("name", ["-rf.java", "@list.java", ".hidden.java"])
    def test_unsafe_names_refused(self, name):
        with pytest.raises(ec.SolutionLoadError, match="unsafe"):
            ec.load_solution_sources(b"class A {}", name)

    def test_missing_file(self, tmp_path):
        with pytest.raises(ec.SolutionLoadError, match="cannot read"):
            ec.load_solution_sources(tmp_path / "nope.java")

    def test_error_messages_never_carry_file_content(self):
        with pytest.raises(ec.SolutionLoadError) as err:
            ec.load_solution_sources(_zip({"A.java": "SENTINEL-CONTENT", "b.py": "x"}), "s.zip")
        assert "SENTINEL" not in str(err.value)

    def test_hash_depends_on_content_and_language_not_order(self):
        a = ec.SolutionSources("java", (ec.SourceFile("A.java", "1"), ec.SourceFile("B.java", "2")))
        b = ec.SolutionSources("java", (ec.SourceFile("B.java", "2"), ec.SourceFile("A.java", "1")))
        c = ec.SolutionSources("java", (ec.SourceFile("A.java", "1"), ec.SourceFile("B.java", "3")))
        assert a.sha256 == b.sha256 != c.sha256
