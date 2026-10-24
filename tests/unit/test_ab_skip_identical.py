#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Per-suite request-identical fast path and the 'identical-skipped' A/B status.

A suite the base does not know, or whose base capture crashes, must only mark THAT suite; everything
else fails closed (crash, timeout, malformed output, different format, changed prompt).
"""

import json
import subprocess
from pathlib import Path

import pytest

from cqc_cpcc.model_eval import prompt_automation as pa
from cqc_cpcc.model_eval import request_capture as rc

pytestmark = pytest.mark.unit

FMT_SETTINGS = {"registry": {"r": 1}, "client": {"c": 1}}


def _cap(*suites, change=()):
    return {"format": rc.FORMAT, "settings": FMT_SETTINGS,
            "suites": {s: {"requests": {"c1": ["x" + ("!" if s in change else "")]}, "calls": {"c1": ["g"]},
                           "outputs": {"c1": "h"}} for s in suites}}


@pytest.fixture
def worktree(tmp_path):
    d = tmp_path / "wt" / "src" / "cqc_cpcc" / "model_eval" / "suites"
    d.mkdir(parents=True)
    (d / "__init__.py").write_text('SUITE_MODULES = {"a": 1, "b": 2, "c": 3}\n')
    return tmp_path / "wt"


class FakeRun:
    """Stands in for the base ``prompt-requests`` subprocess."""

    def __init__(self, bad=(), mode="exit", all_fail=False):
        self.calls, self.bad, self.mode, self.all_fail = [], set(bad), mode, all_fail

    def __call__(self, cmd, *, cwd, env, timeout):
        suites = cmd[cmd.index("--suites") + 1].split(",")
        out = Path(cmd[cmd.index("--out") + 1])
        self.calls.append(suites)
        assert "OPENROUTER_API_KEY" not in env and "GITHUB_OUTPUT" not in env
        if self.mode == "timeout":
            raise subprocess.TimeoutExpired(cmd, timeout)
        if self.all_fail or self.bad & set(suites):
            if self.mode == "garbage":
                out.write_text("{not json")
                return 0, ""
            return 3, "Traceback\nValueError: boom " + "x" * 3000
        out.write_text(json.dumps(_cap(*suites)))
        return 0, ""


class TestCaptureBase:
    def test_a_new_suite_does_not_poison_the_others(self, worktree, tmp_path, monkeypatch):
        fake = FakeRun()
        monkeypatch.setattr(pa, "_run_capture", fake)
        captures, failures = pa.capture_base(worktree, ["a", "brand-new", "b"], tmp_path)
        assert failures == {"brand-new": "new suite: no base side"}
        assert set(captures) == {"a", "b"}
        assert fake.calls == [["a", "b"]]  # the base never saw the suite it does not know

    def test_only_new_suites_means_no_subprocess(self, worktree, tmp_path, monkeypatch):
        fake = FakeRun()
        monkeypatch.setattr(pa, "_run_capture", fake)
        captures, failures = pa.capture_base(worktree, ["x", "y"], tmp_path)
        assert captures == {} and set(failures) == {"x", "y"} and fake.calls == []

    def test_one_suite_crashing_is_isolated_by_the_retry(self, worktree, tmp_path, monkeypatch, capsys):
        fake = FakeRun(bad={"b"})
        monkeypatch.setattr(pa, "_run_capture", fake)
        captures, failures = pa.capture_base(worktree, ["a", "b", "c"], tmp_path)
        assert set(captures) == {"a", "c"}
        assert failures["b"].startswith("base capture failed: exit 3")
        assert sorted(map(tuple, fake.calls)) == [("a",), ("a", "b", "c"), ("b",), ("c",)]
        assert len(capsys.readouterr().out) < 3 * (pa.STDERR_CAP + 400)  # stderr is capped

    def test_every_suite_crashing_fails_every_suite(self, worktree, tmp_path, monkeypatch):
        monkeypatch.setattr(pa, "_run_capture", FakeRun(all_fail=True))
        captures, failures = pa.capture_base(worktree, ["a", "b"], tmp_path)
        assert captures == {} and set(failures) == {"a", "b"}
        assert all(v.startswith("base capture failed") for v in failures.values())

    def test_a_single_crashing_suite_is_not_retried_twice(self, worktree, tmp_path, monkeypatch):
        fake = FakeRun(all_fail=True)
        monkeypatch.setattr(pa, "_run_capture", fake)
        _, failures = pa.capture_base(worktree, ["a"], tmp_path)
        assert fake.calls == [["a"]] and "exit 3" in failures["a"]

    def test_a_timeout_fails_closed_without_a_retry(self, worktree, tmp_path, monkeypatch):
        fake = FakeRun(mode="timeout")
        monkeypatch.setattr(pa, "_run_capture", fake)
        captures, failures = pa.capture_base(worktree, ["a", "b"], tmp_path)
        assert captures == {} and failures == {"a": "base capture failed: timed out",
                                               "b": "base capture failed: timed out"}
        assert len(fake.calls) == 1

    def test_malformed_json_fails_closed(self, worktree, tmp_path, monkeypatch):
        monkeypatch.setattr(pa, "_run_capture", FakeRun(all_fail=True, mode="garbage"))
        captures, failures = pa.capture_base(worktree, ["a", "b"], tmp_path)
        assert captures == {} and all("unreadable capture" in v for v in failures.values())

    def test_json_that_is_not_an_object_fails_closed(self, worktree, tmp_path, monkeypatch):
        def run(cmd, **kw):
            Path(cmd[cmd.index("--out") + 1]).write_text("[1, 2]")
            return 0, ""

        monkeypatch.setattr(pa, "_run_capture", run)
        captures, failures = pa.capture_base(worktree, ["a"], tmp_path)
        assert captures == {} and "not an object" in failures["a"]


@pytest.fixture
def report_for(tmp_path, monkeypatch):
    """requests_identical with the head capture, the base capture and the prompt check faked."""

    def build(head, captures, failures=None, prompts_ok=True, suites=("a", "b", "c")):
        monkeypatch.setattr(rc, "capture", lambda ids: head)
        monkeypatch.setattr(pa, "_base_worktree", lambda ref, where: tmp_path)
        monkeypatch.setattr(pa, "capture_base", lambda *a, **k: (captures, failures or {}))
        monkeypatch.setattr(pa, "prompts_unchanged", lambda s, ref: prompts_ok)
        monkeypatch.setattr(pa.subprocess, "run", lambda *a, **k: None)
        return pa.requests_identical(list(suites), "origin/master", tmp_path / "o")

    return build


class TestRequestsIdenticalPerSuite:
    def test_a_new_suite_loses_only_itself(self, report_for):
        head = _cap("a", "b", "new")
        r = report_for(head, {"a": _cap("a"), "b": _cap("b")}, {"new": "new suite: no base side"},
                       suites=("a", "b", "new"))
        assert r["suites"] == {"a": None, "b": None, "new": "new suite: no base side"}
        assert r["identical_suites"] == ["a", "b"] and r["changed_suites"] == ["new"]
        assert r["identical"] is False  # ALL suites identical, as the workflow reads it
        assert r["verdicts"]["new"] == {"identical": False, "reason": "new suite: no base side"}

    def test_all_identical_keeps_the_old_meaning(self, report_for):
        r = report_for(_cap("a", "b", "c"), {s: _cap(s) for s in "abc"})
        assert r["identical"] is True and r["changed_suites"] == [] and r["identical_suites"] == ["a", "b", "c"]

    def test_a_changed_request_is_in_changed_suites(self, report_for):
        r = report_for(_cap("a", "b", "c", change=("b",)), {s: _cap(s) for s in "abc"})
        assert r["identical_suites"] == ["a", "c"] and r["changed_suites"] == ["b"]
        assert "send different requests" in r["suites"]["b"] and r["identical"] is False

    def test_a_different_format_or_setting_on_one_base_capture_fails_only_that_suite(self, report_for):
        odd = _cap("b")
        odd["format"] = rc.FORMAT + 1
        r = report_for(_cap("a", "b", "c"), {"a": _cap("a"), "b": odd, "c": _cap("c")})
        assert r["suites"]["b"] == "capture formats differ" and r["identical_suites"] == ["a", "c"]
        moved = _cap("c")
        moved["settings"] = {"registry": {"r": 2}, "client": {"c": 1}}
        r = report_for(_cap("a", "b", "c"), {"a": _cap("a"), "b": _cap("b"), "c": moved})
        assert r["suites"]["c"] == "registry differ" and r["identical_suites"] == ["a", "b"]

    def test_a_changed_prompt_is_not_identical(self, report_for):
        r = report_for(_cap("a"), {"a": _cap("a")}, prompts_ok=False, suites=("a",))
        assert r["suites"]["a"] == "a prompt this suite uses changed in the registry"
        assert r["identical_suites"] == []

    def test_a_crashed_suite_is_changed_the_rest_compare(self, report_for):
        r = report_for(_cap("a", "b"), {"a": _cap("a")}, {"b": "base capture failed: exit 3: boom"},
                       suites=("a", "b"))
        assert r["identical_suites"] == ["a"] and r["suites"]["b"].startswith("base capture failed")

    def test_the_report_is_written_for_the_workflow(self, report_for, tmp_path):
        report_for(_cap("a"), {"a": _cap("a")}, suites=("a",))
        saved = json.loads((tmp_path / "o" / "requests.json").read_text())
        assert saved["identical"] is True and saved["suites"] == {"a": None} and saved["identical_suites"] == ["a"]


class TestIdenticalForAb:
    def _run(self, monkeypatch, out, report=None, fail=None):
        def run(cmd, *, cwd, env, timeout):
            assert "OPENROUTER_API_KEY" not in env and "GITHUB_OUTPUT" not in env
            assert timeout == pa.IDENTICAL_CHECK_TIMEOUT_S == 1800
            if fail:
                raise fail
            scratch = Path(cmd[cmd.index("--out") + 1])
            scratch.mkdir(parents=True, exist_ok=True)
            (scratch / "requests.json").write_text(json.dumps(report))
            return 0, ""

        monkeypatch.setattr(pa, "_run_capture", run)

    def test_only_the_identical_suites_are_dropped(self, tmp_path, monkeypatch):
        head = _cap("a", "b", "c", change=("b",))
        self._run(monkeypatch, tmp_path, {"suites": {"a": None, "b": "1 case(s) send different requests", "c": None},
                                          "head": head})
        identical, saved = pa._identical_for_ab(["a", "b", "c"], "origin/master", tmp_path)
        assert identical == ["a", "c"] and set(saved["suites"]) == {"a", "c"}
        assert saved["format"] == rc.FORMAT and saved["settings"] == FMT_SETTINGS
        assert not (tmp_path / "skip-check").exists()

    @pytest.mark.parametrize("fail", [subprocess.TimeoutExpired("x", 1), RuntimeError("exit 1"), OSError("no")])
    def test_any_error_runs_every_suite(self, tmp_path, monkeypatch, fail):
        self._run(monkeypatch, tmp_path, fail=fail)
        assert pa._identical_for_ab(["a"], "origin/master", tmp_path) == ([], {})

    def test_a_missing_or_malformed_report_runs_every_suite(self, tmp_path, monkeypatch):
        self._run(monkeypatch, tmp_path, report={"nothing": 1})
        assert pa._identical_for_ab(["a"], "origin/master", tmp_path) == ([], {})

    def test_an_identical_suite_with_no_head_capture_runs(self, tmp_path, monkeypatch):
        self._run(monkeypatch, tmp_path, {"suites": {"a": None}, "head": {"format": rc.FORMAT, "suites": {}}})
        assert pa._identical_for_ab(["a"], "origin/master", tmp_path) == ([], {})


def _git_head():
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=pa.REPO_ROOT, capture_output=True, text=True,
                          check=True).stdout.strip()


class TestAbWithSkippedSuites:
    @pytest.fixture(autouse=True)
    def test_mode(self, monkeypatch):
        from cqc_cpcc.utilities.AI import llm_gateway

        monkeypatch.setenv("CQC_TEST_MODE", "true")
        monkeypatch.setattr(llm_gateway, "_is_test_mode", lambda: True)
        monkeypatch.delenv(pa.SKIP_IDENTICAL_ENV, raising=False)

    async def test_a_skipped_suite_passes_and_the_changed_one_still_runs(self, tmp_path, monkeypatch):
        saved = {"format": "f", "settings": {}, "suites": {"flowgorithm-grade": {"requests": {}}}}
        monkeypatch.setattr(pa, "_identical_for_ab", lambda *a: (["flowgorithm-grade"], saved))
        report = await pa.prompt_ab(["flowgorithm-grade", "requirement-extraction"], "HEAD", [], tmp_path, repeats=1)
        assert report["suites"]["flowgorithm-grade"]["status"] == pa.IDENTICAL_SKIPPED
        assert report["suites"]["requirement-extraction"]["status"] in ("pass", "pass-provisional")
        assert (tmp_path / "head" / "requirement-extraction" / "raw_outputs.jsonl").exists()
        assert not (tmp_path / "head" / "flowgorithm-grade").exists()  # not run
        assert report["request_capture"] == saved
        md = (tmp_path / "ab.md").read_text()
        assert "identical-skipped" in md and "not re-run, $0" in md
        assert json.loads((tmp_path / "ab.json").read_text())["suites"]["flowgorithm-grade"]["status"] == "identical-skipped"

    async def test_all_skipped_is_a_pass_with_no_model_run(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pa, "_identical_for_ab", lambda *a: (["flowgorithm-grade"], {"suites": {}}))
        report = await pa.prompt_ab(["flowgorithm-grade"], "HEAD", [], tmp_path, repeats=1)
        assert report["status"] == "pass" and not (tmp_path / "head").exists()

    async def test_nothing_is_skipped_when_the_check_doubts(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pa, "_identical_for_ab", lambda *a: ([], {}))
        report = await pa.prompt_ab(["requirement-extraction"], "HEAD", [], tmp_path, repeats=1)
        assert pa.IDENTICAL_SKIPPED not in {r["status"] for r in report["suites"].values()}
        assert "request_capture" not in report

    @pytest.mark.parametrize("how", ["env", "arg", "model"])
    async def test_it_can_be_turned_off_and_an_explicit_model_always_runs(self, tmp_path, monkeypatch, how):
        calls = []
        monkeypatch.setattr(pa, "_identical_for_ab", lambda *a: calls.append(a) or (["requirement-extraction"], {}))
        kwargs = {}
        models = []
        if how == "env":
            monkeypatch.setenv(pa.SKIP_IDENTICAL_ENV, "0")
        elif how == "arg":
            kwargs["skip_identical"] = False
        else:
            from cqc_cpcc.model_eval import prompt_eval

            inc, eff = prompt_eval.incumbent_of(pa.get_suite("requirement-extraction").role)
            models = [prompt_eval.label_of(inc, eff)]
        report = await pa.prompt_ab(["requirement-extraction"], "HEAD", models, tmp_path, repeats=1, **kwargs)
        assert calls == [] and report["suites"]["requirement-extraction"]["status"] != pa.IDENTICAL_SKIPPED

    def test_the_pass_rule_is_unchanged_for_other_statuses(self):
        assert pa.PASSING == ("pass", "identical-skipped")
        md = pa.ab_markdown({"base_ref": "b", "status": "pass", "suites": {
            "s": {"suite": "s", "status": "identical-skipped", "models": {}, "reasons": [], "notes": [pa.SKIP_NOTE]}}})
        assert "identical-skipped" in md and "not re-run, $0" in md


class TestBaseRuleReverifiesTheSkip:
    """ab-base-rule re-captures the BASE side with base code and vetoes a skip it cannot reproduce."""

    def _out(self, tmp_path, capture, status="identical-skipped"):
        out = tmp_path / "artifact"
        out.mkdir()
        report = {"status": "pass", "suites": {"digest": {"suite": "digest", "status": status, "models": {},
                                                          "reasons": [], "notes": []}}}
        if capture is not None:
            report["request_capture"] = capture
        (out / "ab.json").write_text(json.dumps(report))
        return out

    def _decide(self, out):
        sha = _git_head()
        return pa.decide_with_this_rule(out, pa.REPO_ROOT, sha, sha)

    def test_a_reproduced_skip_passes(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pa, "suites_from_data", lambda *a, **k: {"digest": "test"})
        out = self._out(tmp_path, rc.capture(["digest"]))
        decided = self._decide(out)
        assert decided["status"] == "pass" and decided["combined_status"] == "pass"
        assert decided["suites"]["digest"]["status"] == pa.IDENTICAL_SKIPPED

    def test_a_forged_head_capture_fails(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pa, "suites_from_data", lambda *a, **k: {"digest": "test"})
        head = rc.capture(["digest"])
        first = next(iter(head["suites"]["digest"]["requests"]))
        head["suites"]["digest"]["requests"][first] = ["forged"]
        decided = self._decide(self._out(tmp_path, head))
        assert decided["combined_status"] == "fail" and "requests differ" in str(decided["suites"])

    def test_a_skip_with_no_saved_capture_fails(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pa, "suites_from_data", lambda *a, **k: {"digest": "test"})
        assert self._decide(self._out(tmp_path, None))["combined_status"] == "fail"

    def test_a_changed_registry_prompt_fails_the_skip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pa, "suites_from_data", lambda *a, **k: {"digest": "test"})
        monkeypatch.setattr(pa, "same_prompts", lambda *a, **k: False)
        decided = self._decide(self._out(tmp_path, rc.capture(["digest"])))
        assert decided["combined_status"] == "fail" and "changed in the registry" in str(decided["suites"])

    def test_a_base_capture_that_crashes_fails_the_skip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pa, "suites_from_data", lambda *a, **k: {"digest": "test"})
        out = self._out(tmp_path, rc.capture(["digest"]))

        def boom(ids):
            raise RuntimeError("x")

        monkeypatch.setattr(rc, "capture", boom)
        assert self._decide(out)["combined_status"] == "fail"

    def test_a_skip_for_a_suite_the_base_requires_but_the_pr_did_not_skip_still_needs_its_outputs(
            self, tmp_path, monkeypatch):
        monkeypatch.setattr(pa, "suites_from_data", lambda *a, **k: {"digest": "test"})
        out = self._out(tmp_path, None, status="pass")
        assert self._decide(out)["combined_status"] == "fail"  # 'pass' without saved outputs is not a skip


class TestOverridesForceThePaidRun:
    """full_ab and the PROMPT_EVAL_FAST_PATH=off variable reach prompt-ab as FULL_AB / FAST_PATH (dispatch step)."""

    @pytest.fixture(autouse=True)
    def clean_env(self, monkeypatch):
        for name in (pa.SKIP_IDENTICAL_ENV, "FULL_AB", "FAST_PATH"):
            monkeypatch.delenv(name, raising=False)

    def test_the_pr_ab_job_exports_none_of_them_so_the_skip_is_on(self):
        assert pa.skip_identical_enabled() is True

    @pytest.mark.parametrize("name,value", [("FULL_AB", "true"), ("FULL_AB", "TRUE"), ("FAST_PATH", "off"),
                                            ("FAST_PATH", "OFF"), (pa.SKIP_IDENTICAL_ENV, "0"),
                                            (pa.SKIP_IDENTICAL_ENV, "off")])
    def test_each_override_turns_the_skip_off(self, monkeypatch, name, value):
        monkeypatch.setenv(name, value)
        assert pa.skip_identical_enabled() is False

    @pytest.mark.parametrize("name,value", [("FULL_AB", "false"), ("FULL_AB", ""), ("FAST_PATH", "on"),
                                            ("FAST_PATH", "")])
    def test_other_values_leave_it_on(self, monkeypatch, name, value):
        monkeypatch.setenv(name, value)
        assert pa.skip_identical_enabled() is True

    async def test_prompt_ab_runs_everything_paid_under_full_ab(self, tmp_path, monkeypatch):
        from cqc_cpcc.utilities.AI import llm_gateway

        monkeypatch.setenv("CQC_TEST_MODE", "true")
        monkeypatch.setattr(llm_gateway, "_is_test_mode", lambda: True)
        monkeypatch.setenv("FULL_AB", "true")
        calls = []
        monkeypatch.setattr(pa, "_identical_for_ab", lambda *a: calls.append(a) or (["requirement-extraction"], {}))
        report = await pa.prompt_ab(["requirement-extraction"], "HEAD", [], tmp_path, repeats=1)
        assert calls == [] and report["suites"]["requirement-extraction"]["status"] != pa.IDENTICAL_SKIPPED

    async def test_no_base_worktree_is_created_when_every_suite_is_skipped(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pa, "_identical_for_ab", lambda *a: (["flowgorithm-grade"], {"suites": {}}))

        def no_worktree(*a, **k):
            raise AssertionError("a base worktree was created")

        monkeypatch.setattr(pa, "_base_worktree", no_worktree)
        report = await pa.prompt_ab(["flowgorithm-grade"], "HEAD", [], tmp_path, repeats=1)
        assert report["status"] == "pass"


class TestCaptureProcessGroup:
    def test_a_timeout_kills_the_whole_process_group(self, tmp_path):
        import os
        import sys
        import time

        pidfile = tmp_path / "child.pid"
        code = ("import subprocess, sys, time; "
                "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)']); "
                f"open({str(pidfile)!r}, 'w').write(str(p.pid)); time.sleep(120)")
        with pytest.raises(subprocess.TimeoutExpired):
            pa._run_capture([sys.executable, "-c", code], cwd=tmp_path, env=dict(os.environ), timeout=3)
        child = int(pidfile.read_text())
        for _ in range(50):
            try:
                os.kill(child, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            os.kill(child, 9)
            pytest.fail("the grandchild outlived the timeout")

    def test_exit_code_and_stderr_come_back(self, tmp_path):
        import os
        import sys

        code, err = pa._run_capture([sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(4)"],
                                    cwd=tmp_path, env=dict(os.environ), timeout=30)
        assert (code, err) == (4, "boom")


class TestMixedReportInTheBaseRule:
    """One verified skip next to one suite the A/B really ran: each is decided its own way."""

    def test_a_verified_skip_and_a_run_suite_both_count(self, tmp_path, monkeypatch):
        from cqc_cpcc.utilities.AI import llm_gateway

        monkeypatch.setenv("CQC_TEST_MODE", "true")
        monkeypatch.setattr(llm_gateway, "_is_test_mode", lambda: True)
        monkeypatch.setenv("CQC_AB_SKIP_IDENTICAL", "0")
        import asyncio

        report = asyncio.run(pa.prompt_ab(["requirement-extraction"], "HEAD", [], tmp_path))
        if report["suites"]["requirement-extraction"]["status"] == "no-base":
            pytest.skip("no base run in this checkout")
        ab = json.loads((tmp_path / "ab.json").read_text())
        ab["suites"]["digest"] = {"suite": "digest", "status": pa.IDENTICAL_SKIPPED, "models": {}, "reasons": [],
                                  "notes": []}
        ab["request_capture"] = rc.capture(["digest"])
        (tmp_path / "ab.json").write_text(json.dumps(ab))
        monkeypatch.setattr(pa, "suites_from_data",
                            lambda *a, **k: {"requirement-extraction": "t", "digest": "t"})
        sha = _git_head()
        decided = pa.decide_with_this_rule(tmp_path, pa.REPO_ROOT, sha, sha)
        assert decided["suites"]["digest"]["status"] == pa.IDENTICAL_SKIPPED
        assert decided["suites"]["requirement-extraction"]["status"] in ("pass", "pass-provisional")
        assert decided["status"] in ("pass", "pass-provisional") and decided["combined_status"] != "fail"
        # a forged capture vetoes the skipped suite on its own; the run suite keeps its result
        requests = ab["request_capture"]["suites"]["digest"]["requests"]
        requests[next(iter(requests))] = ["forged"]
        (tmp_path / "ab.json").write_text(json.dumps(ab))
        decided = pa.decide_with_this_rule(tmp_path, pa.REPO_ROOT, sha, sha)
        assert decided["suites"]["digest"]["status"] == "fail"
        assert decided["suites"]["requirement-extraction"]["status"] in ("pass", "pass-provisional")
        assert decided["combined_status"] == "fail"
