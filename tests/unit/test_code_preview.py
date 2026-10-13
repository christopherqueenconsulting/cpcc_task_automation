#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for the Code tab's pure helpers (synthetic code only)."""

import json
import re

import pytest

from cqc_cpcc.utilities.code_preview import (
    ONECOMPILER_ORIGIN,
    build_onecompiler_payload,
    build_runner_html,
    collect_code_files,
    onlinegdb_url,
    read_source_files,
    runner_language,
)

MAIN_JAVA = "public class Main {\n    public static void main(String[] args) {\n        new Shape();\n    }\n}\n"
SHAPE_JAVA = "public class Shape {\n    double area() { return 0; }\n}\n"
CIRCLE_JAVA = "public class Circle extends Shape {\n}\n"


@pytest.mark.unit
class TestCollectCodeFiles:
    def test_the_file_with_main_comes_first(self):
        files, skipped = collect_code_files(
            {"Circle.java": CIRCLE_JAVA, "Shape.java": SHAPE_JAVA, "Main.java": MAIN_JAVA})

        assert [f.name for f in files] == ["Main.java", "Circle.java", "Shape.java"]
        assert skipped == []
        assert files[0].language == "java"
        assert files[0].line_count == 5

    def test_reads_paths_on_disk(self, tmp_path):
        path = tmp_path / "Shape.java"
        path.write_text(SHAPE_JAVA)

        files, _ = collect_code_files({"Shape.java": str(path)})

        assert files[0].content == SHAPE_JAVA

    def test_documents_and_media_are_listed_not_shown(self):
        files, skipped = collect_code_files({
            "Main.java": MAIN_JAVA, "Report.docx": "/tmp/nope.docx",
            "demo.mp4": "/tmp/nope.mp4", "notes.txt": "Ran it twice.",
        })

        assert [f.name for f in files] == ["Main.java", "notes.txt"]
        assert skipped == ["demo.mp4", "Report.docx"]

    def test_long_files_are_cut_and_flagged(self):
        files, _ = collect_code_files({"Big.java": "x" * 50}, max_chars=10)

        assert files[0].truncated and files[0].content == "x" * 10

    def test_nested_paths_show_the_file_name(self):
        files, _ = collect_code_files({"src/app/Main.java": MAIN_JAVA})

        assert files[0].name == "Main.java"

    def test_nothing_to_show(self):
        assert collect_code_files(None) == ([], [])
        assert collect_code_files({"Empty.java": "   "}) == ([], [])


@pytest.mark.unit
class TestRunnerLanguage:
    @pytest.mark.parametrize("names, expected", [
        (["Main.java", "Shape.java"], "java"),
        (["main.cpp", "shape.h"], "cpp"),
        (["main.c", "util.h"], "c"),
        (["app.py"], "python"),
        (["Program.cs"], "csharp"),
        (["analysis.sas"], None),
        (["notes.txt"], None),
        (["Main.java", "helper.py"], None),
    ])
    def test_language(self, names, expected):
        files, _ = collect_code_files({n: "int main() { return 0; }" for n in names})

        assert runner_language(files) == expected


@pytest.mark.unit
class TestOneCompilerPayload:
    def test_every_java_file_is_sent_by_name(self):
        files, _ = collect_code_files({"Shape.java": SHAPE_JAVA, "Main.java": MAIN_JAVA,
                                       "notes.txt": "hi"})

        payload = build_onecompiler_payload(files, "java")

        assert payload["eventType"] == "populateCode" and payload["language"] == "java"
        assert [f["name"] for f in payload["files"]] == ["Main.java", "Shape.java"]
        assert payload["files"][0]["content"] == MAIN_JAVA

    def test_cpp_sends_headers_too(self):
        files, _ = collect_code_files({"main.cpp": "int main() {}", "shape.h": "struct S {};"})

        assert {f["name"] for f in build_onecompiler_payload(files, "cpp")["files"]} == \
            {"main.cpp", "shape.h"}

    def test_truncated_files_are_not_sent(self):
        files, _ = collect_code_files({"Main.java": MAIN_JAVA, "Big.java": "y" * 500},
                                      max_chars=400)

        assert [f["name"] for f in build_onecompiler_payload(files, "java")["files"]] == ["Main.java"]


@pytest.mark.unit
class TestRunnerHtml:
    def test_student_code_cannot_close_the_script(self):
        evil = 'class X { String s = "</script><script>alert(1)</script>"; }'
        html = build_runner_html({"eventType": "populateCode", "language": "java",
                                  "files": [{"name": "X.java", "content": evil}]})

        assert "</script><script>alert(1)" not in html
        assert html.count("</script>") == 1
        data = re.search(r"const payload = (.*);\n", html).group(1)
        assert json.loads(data)["files"][0]["content"] == evil

    def test_posts_only_to_onecompiler(self):
        html = build_runner_html({"eventType": "populateCode", "language": "cpp", "files": []})

        assert f'postMessage(payload, "{ONECOMPILER_ORIGIN}")' in html
        assert '"*"' not in html
        assert f'src="{ONECOMPILER_ORIGIN}/embed/cpp?listenToEvents=true' in html


@pytest.mark.unit
@pytest.mark.parametrize("lang, page", [
    ("java", "online_java_compiler"), ("cpp", "online_c++_compiler"), ("c", "online_c_compiler"),
    ("python", "online_python_compiler"), ("csharp", "online_csharp_compiler"),
])
def test_onlinegdb_url(lang, page):
    assert onlinegdb_url(lang) == f"https://www.onlinegdb.com/{page}"


@pytest.mark.unit
def test_read_source_files_skips_unreadable_and_empty(tmp_path, mocker):
    good = tmp_path / "A.java"
    good.write_text("class A {}")

    assert read_source_files({"A.java": str(good), "B.java": "", "C.java": None}) == [
        ("A.java", "class A {}")]
    mocker.patch("builtins.open", side_effect=PermissionError("denied"))
    assert read_source_files({"A.java": str(good)}) == []
