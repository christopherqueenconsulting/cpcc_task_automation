#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""The background grading job: states, Cancel, deadline, concurrency, saves, token registry.

Synthetic student names only. The model call is a fake ``grade`` function, except in the last
test, which drives the real gateway in test mode with ``CQC_TEST_MODE_LATENCY_S``.
"""

import ast
import asyncio
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from cqc_cpcc import grading_job
from cqc_cpcc.grading_job import GradingJob
from cqc_cpcc.rubric_models import CriterionResult, RubricAssessmentResult
from cqc_cpcc.run_locks import run_lock
from cqc_cpcc.utilities.AI import llm_progress
from cqc_cpcc.utilities.zip_grading_utils import StudentSubmission

pytestmark = pytest.mark.unit

RUN_KEY = "a1b2c3"
KWARGS = dict(rubric=None, assignment_instructions="Write a program.", reference_solution=None,
              error_definitions=None, model_name="vendor/model-one", temperature=0.0, requirements=None,
              effort="high")


def _result(earned=90, label="vendor/model-one@high", needs_review=False):
    return RubricAssessmentResult(
        rubric_id="r", rubric_version="1", total_points_possible=100, total_points_earned=earned,
        criteria_results=[CriterionResult(criterion_id="c", criterion_name="C", points_possible=100,
                                          points_earned=earned, feedback="ok")],
        overall_feedback="fine", graded_with_model=label, needs_review=needs_review,
        validity_status="empty" if needs_review else "ok")


def _submissions(tmp_path, names):
    out = {}
    for name in names:
        f = tmp_path / f"{name.replace(' ', '_')}.java"
        f.write_text(name)  # the file's text is the student's name, so the fake grader can tell them apart
        out[name] = StudentSubmission(student_id=name, student_name="", files={f.name: str(f)})
    return out


class Saves:
    def __init__(self):
        self.calls = []

    def __call__(self, run_key, results, failures, **kwargs):
        self.calls.append((run_key, [sid for sid, _ in results], list(failures), kwargs))


def _job(tmp_path, names, behave, **extra):
    """A job whose fake model call does ``behave(student_name)`` (an async function)."""
    async def grade(student_submission, **kwargs):
        return await behave(student_submission, **kwargs)
    return GradingJob(RUN_KEY, _submissions(tmp_path, names), dict(KWARGS), grade=grade,
                      deadline_s=extra.pop("deadline_s", 30), concurrency=extra.pop("concurrency", 4), **extra)


def _wait_for(predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _state(job, name):
    return next(r.state for r in job.snapshot().rows if r.student_id == name)


async def _ok(text, **kwargs):
    return _result()


def test_a_job_finishes_every_student_and_saves_each_one(tmp_path):
    saves = Saves()
    job = _job(tmp_path, ["Student A", "Student B", "Student C"], _ok, save=saves,
               greeting_names={"Student A": ""}, meta={"model": "m"})
    job.start()
    assert job.wait(10)
    snap = job.snapshot()
    assert snap.done and snap.finished == 3 and snap.counts[grading_job.DONE] == 3
    out = job.outcome()
    assert [sid for sid, _ in out.results] == ["Student A", "Student B", "Student C"]
    assert out.failures == []
    # One save after each finished student, then the final one.
    assert len(saves.calls) >= 4
    assert saves.calls[-1][1] == ["Student A", "Student B", "Student C"] and saves.calls[-1][2] == []
    assert saves.calls[-1][3] == {"greeting_names": {"Student A": ""}, "meta": {"model": "m"}}
    assert all(call[0] == RUN_KEY for call in saves.calls)


def test_cancel_keeps_the_finished_students(tmp_path):
    release = threading.Event()

    async def behave(text, **kwargs):
        if "Student B" in text:
            await asyncio.get_running_loop().run_in_executor(None, release.wait, 30)
        return _result()

    saves = Saves()
    job = _job(tmp_path, ["Student A", "Student B", "Student C"], behave, save=saves, concurrency=2)
    job.start()
    assert _wait_for(lambda: _state(job, "Student A") == grading_job.DONE)
    assert _wait_for(lambda: _state(job, "Student B") in grading_job.WAITING + (grading_job.PREPARING,))
    job.cancel()
    release.set()
    assert job.wait(10)
    states = {r.student_id: r.state for r in job.snapshot().rows}
    assert states["Student A"] == grading_job.DONE
    assert states["Student B"] == grading_job.CANCELLED
    assert states["Student C"] in (grading_job.DONE, grading_job.CANCELLED)
    out = job.outcome()
    assert "Student A" in [sid for sid, _ in out.results]
    assert "Student B" in out.failures
    last = saves.calls[-1]
    assert "Student A" in last[1] and "Student B" in last[2]


def test_cancelling_one_student_leaves_the_others_running(tmp_path):
    started = threading.Event()

    async def behave(text, **kwargs):
        if "Student B" in text:
            started.set()
            await asyncio.sleep(60)
        return _result()

    job = _job(tmp_path, ["Student A", "Student B", "Student C"], behave)
    job.start()
    assert started.wait(5)
    job.cancel_student("Student B")
    assert job.wait(10)
    states = {r.student_id: r.state for r in job.snapshot().rows}
    assert states == {"Student A": grading_job.DONE, "Student B": grading_job.CANCELLED,
                      "Student C": grading_job.DONE}
    assert job.outcome().failures == ["Student B"]


def test_a_student_past_the_deadline_is_timed_out_and_the_rest_finish(tmp_path):
    async def behave(text, **kwargs):
        if "Student B" in text:
            await asyncio.sleep(60)
        return _result()

    job = _job(tmp_path, ["Student A", "Student B"], behave, deadline_s=2.0)
    job.start()
    assert job.wait(10)
    states = {r.student_id: r for r in job.snapshot().rows}
    assert states["Student B"].state == grading_job.TIMED_OUT
    assert "No result within" in states["Student B"].last_issue
    assert states["Student A"].state == grading_job.DONE
    assert job.outcome().failures == ["Student B"]
    assert job.snapshot().counts[grading_job.TIMED_OUT] == 1


def test_no_more_than_the_concurrency_limit_run_at_once(tmp_path):
    lock = threading.Lock()
    running = {"now": 0, "peak": 0}

    async def behave(text, **kwargs):
        with lock:
            running["now"] += 1
            running["peak"] = max(running["peak"], running["now"])
        await asyncio.sleep(0.05)
        with lock:
            running["now"] -= 1
        return _result()

    job = _job(tmp_path, [f"Student {c}" for c in "ABCDEFGH"], behave, concurrency=3)
    job.start()
    assert job.wait(10)
    assert running["peak"] == 3
    assert job.snapshot().counts[grading_job.DONE] == 8


def test_model_events_update_the_row(tmp_path):
    gate = threading.Event()

    async def behave(text, **kwargs):
        llm_progress.emit("attempt_started", "vendor/model-one", attempt=1, max_attempts=3, timeout_s=180)
        llm_progress.emit("attempt_failed", "vendor/model-one", attempt=1, max_attempts=3,
                          error="timed out after 180 s", will_retry=True)
        llm_progress.emit("attempt_started", "vendor/model-one", attempt=2, max_attempts=3, timeout_s=180)
        await asyncio.get_running_loop().run_in_executor(None, gate.wait, 30)
        llm_progress.emit("succeeded", "vendor/model-one", attempt=2, max_attempts=3)
        return _result()

    job = _job(tmp_path, ["Student A"], behave)
    job.start()
    assert _wait_for(lambda: _state(job, "Student A") == grading_job.RETRYING)
    row = job.snapshot().rows[0]
    assert row.model == "vendor/model-one" and row.attempt == "2/3"
    assert "Try 1 did not work. Trying again." == row.last_issue
    assert row.status == "Trying again"
    gate.set()
    assert job.wait(10)
    done = job.snapshot().rows[0]
    assert done.state == grading_job.DONE and done.attempt == "" and done.waiting_s == 0
    assert done.score_label == "90/100"


def test_the_fallback_event_is_shown_as_a_plain_sentence(tmp_path):
    gate = threading.Event()

    async def behave(text, **kwargs):
        llm_progress.emit("attempt_started", "vendor/model-one", attempt=1, max_attempts=2)
        llm_progress.emit("fallback", "vendor/model-one")
        llm_progress.emit("attempt_started", "vendor/model-two", attempt=1, max_attempts=2)
        await asyncio.get_running_loop().run_in_executor(None, gate.wait, 30)
        return _result()

    job = _job(tmp_path, ["Student A"], behave)
    job.start()
    assert _wait_for(lambda: job.snapshot().rows[0].model == "vendor/model-two")
    row = job.snapshot().rows[0]
    assert row.state == grading_job.RETRYING
    assert row.last_issue == "The first model did not answer. Switching to a backup model."
    gate.set()
    assert job.wait(10)


def test_cancelling_before_start_and_after_the_end_is_safe(tmp_path):
    job = _job(tmp_path, ["Student A", "Student B"], _ok)
    job.cancel_student("Student A")
    job.cancel_student("Nobody")
    job.start()
    assert job.wait(10)
    states = {r.student_id: r.state for r in job.snapshot().rows}
    assert states["Student A"] == grading_job.CANCELLED
    assert states["Student B"] == grading_job.DONE

    job.cancel()  # after the end: nothing happens
    job.cancel_student("Student B")
    assert {r.student_id: r.state for r in job.snapshot().rows} == states

    early = _job(tmp_path, ["Student A", "Student B"], _ok)
    early.cancel()
    early.start()
    assert early.wait(10)
    assert early.snapshot().counts[grading_job.CANCELLED] == 2
    assert early.outcome().failures == ["Student A", "Student B"]


def test_a_failing_student_gets_a_plain_issue_and_never_the_exception_text(tmp_path):
    async def behave(text, **kwargs):
        if "Student B" in text:
            raise RuntimeError("SENTINEL student code def main():")
        return _result()

    job = _job(tmp_path, ["Student A", "Student B"], behave)
    job.start()
    assert job.wait(10)
    row = next(r for r in job.snapshot().rows if r.student_id == "Student B")
    assert row.state == grading_job.FAILED
    assert "SENTINEL" not in row.last_issue and "Retry it" in row.last_issue
    assert job.outcome().failures == ["Student B"]


def test_a_result_that_needs_review_is_kept_and_marked(tmp_path):
    async def behave(text, **kwargs):
        return _result(0, needs_review=True)

    job = _job(tmp_path, ["Student A"], behave)
    job.start()
    assert job.wait(10)
    row = job.snapshot().rows[0]
    assert row.state == grading_job.NEEDS_REVIEW and row.status == "Done, needs your review"
    assert [sid for sid, _ in job.outcome().results] == ["Student A"]


def test_the_result_is_stamped_with_the_model_and_effort_when_the_grader_did_not(tmp_path):
    async def behave(text, **kwargs):
        llm_progress.emit("succeeded", "vendor/model-two", attempt=1, max_attempts=2)
        return _result(label=None)

    job = _job(tmp_path, ["Student A"], behave)
    job.start()
    assert job.wait(10)
    assert job.outcome().results[0][1].graded_with_model == "vendor/model-two@high"


def test_a_rerun_merges_into_the_runs_stored_results(tmp_path):
    job = _job(tmp_path, ["Student B"], _ok, base_results=[("Student A", _result(70))],
               base_failures=["Student B", "Student C"])
    job.start()
    assert job.wait(10)
    out = job.outcome()
    assert [sid for sid, _ in out.results] == ["Student A", "Student B"]
    assert out.failures == ["Student C"]


def test_every_save_waits_for_the_run_lock(tmp_path):
    saves = Saves()
    job = _job(tmp_path, ["Student A"], _ok, save=saves)
    with run_lock(RUN_KEY):
        job.start()
        time.sleep(0.4)
        assert saves.calls == []  # the worker is waiting for the lock another change holds
    assert job.wait(10)
    assert saves.calls


def test_a_save_error_does_not_stop_the_job(tmp_path):
    def broken(*args, **kwargs):
        raise OSError("disk full")

    job = _job(tmp_path, ["Student A"], _ok, save=broken)
    job.start()
    assert job.wait(10)
    assert job.snapshot().counts[grading_job.DONE] == 1


def test_results_are_in_the_saved_runs_even_if_the_page_never_consumes_the_job(tmp_path, monkeypatch):
    from cqc_streamlit_app import results_store

    monkeypatch.setenv("CQC_GRADING_RESULTS_DIR", str(tmp_path / "saved"))

    def save(run_key, results, failures, **kwargs):
        results_store.save_run(run_key, "rubric", results, failures, **kwargs)

    job = _job(tmp_path, ["Student A", "Student B"], _ok, save=save, greeting_names={"Student A": ""})
    job.start()
    assert job.wait(10)
    runs = results_store.load_runs()
    assert [r["run_key"] for r in runs] == [RUN_KEY]
    assert [sid for sid, _ in runs[0]["results"]] == ["Student A", "Student B"]
    assert runs[0]["failures"] == []


# ------------------------------------------------------------------ registry

def test_only_the_token_reaches_a_job_and_a_finished_job_expires(tmp_path, monkeypatch):
    job = _job(tmp_path, ["Student A"], _ok)
    token = grading_job.start_job(job)
    assert len(token) == 32 and job.token == token
    assert grading_job.attach(token) is job
    for bad in (None, "", "x", token[:-1], token.upper(), RUN_KEY, 12, ["a"], token + "0"):
        assert grading_job.attach(bad) is None
    assert job.wait(10)
    monkeypatch.setattr(grading_job, "JOB_TTL_S", 0.0)
    time.sleep(0.01)
    assert grading_job.attach(token) is None  # expired
    assert grading_job.attach(token) is None

    other = _job(tmp_path, ["Student A"], _ok)
    other_token = grading_job.start_job(other)
    assert other_token != token
    assert other.wait(10)
    grading_job.release(other_token)
    assert grading_job.attach(other_token) is None


def test_the_registry_has_no_listing():
    public = [n for n in dir(grading_job) if not n.startswith("_")]
    assert not [n for n in public if n.lower() in ("jobs", "all_jobs", "list_jobs", "jobs_for", "find", "for_run")]


# ------------------------------------------------------------------ module rules

def test_the_module_imports_nothing_from_streamlit():
    source = Path(grading_job.__file__).read_text()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            assert not [a for a in node.names if a.name.split(".")[0] == "streamlit"]
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] not in ("streamlit", "cqc_streamlit_app")
    code = ("import sys, cqc_cpcc.grading_job, cqc_cpcc.run_locks; "
            "sys.exit(1 if any(m == 'streamlit' or m.startswith('streamlit.') for m in sys.modules) else 0)")
    done = subprocess.run([sys.executable, "-c", code], env={**__import__("os").environ, "PYTHONPATH": str(
        Path(grading_job.__file__).resolve().parents[1])}, capture_output=True)
    assert done.returncode == 0, done.stderr.decode()[-500:]


# ------------------------------------------------------------------ test mode

def test_cancel_stops_a_real_gateway_call_that_is_waiting(tmp_path, monkeypatch):
    """The gateway in test mode with CQC_TEST_MODE_LATENCY_S: a call that would wait 20 s ends in
    well under that when the job is cancelled, and the student is listed for a retry."""
    from cqc_cpcc import rubric_grading
    from cqc_cpcc.rubric_config import get_rubric_by_id, list_available_rubrics

    def no_real_call(*args, **kwargs):
        raise AssertionError("a real model call was attempted")

    # The module object rubric_grading itself calls, whatever other tests did to sys.modules.
    gateway = rubric_grading.llm_gateway
    monkeypatch.setattr(gateway, "_is_test_mode", lambda: True)
    monkeypatch.setattr(gateway, "_complete", no_real_call)
    monkeypatch.setenv("CQC_TEST_MODE_LATENCY_S", "20")
    rubric = get_rubric_by_id(list_available_rubrics()[0])
    code = "public class Main {\n    public static void main(String[] a) {\n        System.out.println(1);\n    }\n}\n"
    subs = {}
    for name in ("Student A", "Student B"):
        f = tmp_path / f"{name[-1]}.java"
        f.write_text(code)
        subs[name] = StudentSubmission(student_id=name, student_name="", files={f.name: str(f)})
    job = GradingJob(RUN_KEY, subs, {**KWARGS, "rubric": rubric, "model_name": None, "effort": None},
                     concurrency=2)
    started = time.monotonic()
    job.start()
    assert _wait_for(lambda: any(r.state == grading_job.CALLING for r in job.snapshot().rows), 10)
    waiting = [r for r in job.snapshot().rows if r.state == grading_job.CALLING]
    assert waiting and waiting[0].model and waiting[0].attempt == "1/1"
    job.cancel()
    assert job.wait(10)
    assert time.monotonic() - started < 15
    assert job.snapshot().counts[grading_job.CANCELLED] == 2
    assert job.outcome().failures == ["Student A", "Student B"]


def test_active_count_counts_only_unfinished_jobs(tmp_path):
    gate = threading.Event()

    async def behave(text, **kwargs):
        await asyncio.get_running_loop().run_in_executor(None, gate.wait, 30)
        return _result()

    before = grading_job.active_count()
    job = _job(tmp_path, ["Student A"], behave)
    grading_job.start_job(job)
    assert grading_job.active_count() == before + 1
    gate.set()
    assert job.wait(10)
    assert grading_job.active_count() == before


def test_the_process_wide_call_limit_is_shared_by_jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(grading_job, "_CALL_LIMIT", threading.BoundedSemaphore(1))
    lock = threading.Lock()
    running = {"now": 0, "peak": 0}

    async def behave(text, **kwargs):
        with lock:
            running["now"] += 1
            running["peak"] = max(running["peak"], running["now"])
        await asyncio.sleep(0.05)
        with lock:
            running["now"] -= 1
        return _result()

    job = _job(tmp_path, [f"Student {c}" for c in "ABCD"], behave, concurrency=4)
    job.start()
    assert job.wait(10)
    assert running["peak"] == 1
    assert job.snapshot().counts[grading_job.DONE] == 4


def test_the_call_slot_is_never_leaked_by_cancel_timeout_or_error(tmp_path, monkeypatch):
    sem = threading.BoundedSemaphore(2)
    monkeypatch.setattr(grading_job, "_CALL_LIMIT", sem)
    gate = threading.Event()

    async def hold(text, **kwargs):
        await asyncio.get_running_loop().run_in_executor(None, gate.wait, 30)
        if "Student D" in text:
            raise RuntimeError("boom")
        return _result()

    # Two holders, two queued students that are cancelled while they wait for a slot.
    job = _job(tmp_path, ["Student A", "Student B", "Student C", "Student X"], hold, concurrency=4)
    job.start()
    assert _wait_for(lambda: sum(r.state == grading_job.CALLING or r.state == grading_job.PREPARING
                                 for r in job.snapshot().rows) >= 2)
    time.sleep(0.2)
    job.cancel_student("Student C")
    job.cancel_student("Student X")
    time.sleep(0.2)
    gate.set()
    assert job.wait(10)
    for _ in range(2):
        assert sem.acquire(blocking=False)
    assert not sem.acquire(blocking=False)
    sem.release()
    sem.release()


def test_a_wait_for_a_stuck_slot_ends_at_the_deadline_and_releases_nothing(tmp_path, monkeypatch):
    sem = threading.BoundedSemaphore(1)
    sem.acquire()  # someone else holds the only slot
    monkeypatch.setattr(grading_job, "_CALL_LIMIT", sem)
    job = _job(tmp_path, ["Student A"], _ok, deadline_s=0.3)
    job.start()
    assert job.wait(10)
    assert job.snapshot().counts[grading_job.TIMED_OUT] == 1
    sem.release()
    assert sem.acquire(blocking=False)


def test_an_error_in_the_call_releases_the_slot(tmp_path, monkeypatch):
    sem = threading.BoundedSemaphore(1)
    monkeypatch.setattr(grading_job, "_CALL_LIMIT", sem)

    async def boom(text, **kwargs):
        raise RuntimeError("x")

    job = _job(tmp_path, ["Student A"], boom)
    job.start()
    assert job.wait(10)
    assert sem.acquire(blocking=False)


def test_a_job_that_fails_to_start_is_not_counted_as_live(tmp_path, monkeypatch):
    job = _job(tmp_path, ["Student A"], _ok)
    before = grading_job.active_count()
    monkeypatch.setattr(job, "start", lambda: (_ for _ in ()).throw(RuntimeError("no thread")))
    with pytest.raises(RuntimeError):
        grading_job.start_job(job)
    assert grading_job.active_count() == before


def test_a_cleared_run_is_not_brought_back_by_a_later_save(tmp_path, monkeypatch):
    from cqc_streamlit_app import results_store

    monkeypatch.setenv("CQC_GRADING_RESULTS_DIR", str(tmp_path / "saved"))
    gate = threading.Event()

    async def behave(text, **kwargs):
        if "Student B" in text:
            await asyncio.get_running_loop().run_in_executor(None, gate.wait, 30)
        return _result()

    def save(run_key, results, failures, **kwargs):
        results_store.save_run(run_key, "rubric", results, failures, **kwargs)

    job = _job(tmp_path, ["Student A", "Student B"], behave, save=save, load=results_store.load_run)
    job.start()
    assert _wait_for(lambda: results_store.load_run(RUN_KEY) is not None)
    results_store.delete_run(RUN_KEY)  # Clear results
    gate.set()
    assert job.wait(10)
    assert results_store.load_run(RUN_KEY) is None


# --- warm-first (CQC_GRADING_WARM_FIRST): the first student alone, then the rest ------------------

def _ordered_job(tmp_path, names, first_fails=False, **extra):
    """A job whose first student waits on a gate; ``log`` records the order of starts and ends."""
    gate = threading.Event()
    log = []

    async def behave(text, **kwargs):
        text = next(n for n in names if n in text)  # the submission text carries the student's name
        log.append(("start", text))
        if text == names[0]:
            await asyncio.get_running_loop().run_in_executor(None, gate.wait, 30)
            if first_fails:
                raise RuntimeError("model said no")
        log.append(("end", text))
        return _result()

    return _job(tmp_path, names, behave, **extra), gate, log


def test_warm_first_holds_the_rest_until_the_first_student_finishes(tmp_path):
    names = ["Student A", "Student B", "Student C", "Student D"]
    job, gate, log = _ordered_job(tmp_path, names, warm_first=True)
    job.start()
    assert _wait_for(lambda: ("start", "Student A") in log)
    time.sleep(0.2)  # the others would have started by now if they were not held
    assert log == [("start", "Student A")]
    gate.set()
    assert job.wait(10)
    assert log.index(("end", "Student A")) < min(log.index(("start", n)) for n in names[1:])
    assert job.snapshot().counts[grading_job.DONE] == 4


def test_warm_first_releases_the_rest_when_the_first_student_fails(tmp_path):
    names = ["Student A", "Student B", "Student C"]
    job, gate, log = _ordered_job(tmp_path, names, first_fails=True, warm_first=True)
    job.start()
    assert _wait_for(lambda: ("start", "Student A") in log)
    gate.set()
    assert job.wait(10)
    counts = job.snapshot().counts
    assert counts[grading_job.FAILED] == 1 and counts[grading_job.DONE] == 2


def test_by_default_every_student_starts_without_waiting_for_the_first(tmp_path, monkeypatch):
    monkeypatch.delenv("CQC_GRADING_WARM_FIRST", raising=False)
    names = ["Student A", "Student B", "Student C"]
    job, gate, log = _ordered_job(tmp_path, names)
    job.start()
    assert _wait_for(lambda: {("start", n) for n in names} <= set(log))  # all started, A still gated
    assert ("end", "Student A") not in log
    gate.set()
    assert job.wait(10)


def test_the_warm_first_flag_reads_the_environment(monkeypatch):
    for value, expected in (("on", True), ("1", True), ("true", True), ("", False), ("off", False)):
        monkeypatch.setenv("CQC_GRADING_WARM_FIRST", value)
        assert grading_job.warm_first_enabled() is expected


def test_warm_first_goes_on_after_the_timeout_when_the_first_student_never_answers(tmp_path):
    names = ["Student A", "Student B", "Student C"]
    job, gate, log = _ordered_job(tmp_path, names, warm_first=True, warm_timeout_s=0.3)
    job.start()
    assert _wait_for(lambda: {("end", "Student B"), ("end", "Student C")} <= set(log))  # A is still gated
    assert ("end", "Student A") not in log
    gate.set()
    assert job.wait(10)
    assert job.snapshot().counts[grading_job.DONE] == 3


def test_a_single_student_job_is_not_held_by_warm_first(tmp_path):
    job, gate, log = _ordered_job(tmp_path, ["Student A"], warm_first=True)
    job.start()
    assert _wait_for(lambda: ("start", "Student A") in log)
    gate.set()
    assert job.wait(10)
