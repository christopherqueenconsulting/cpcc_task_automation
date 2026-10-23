#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""A rubric grading batch that runs on a background thread, so the page can show it live
and stop it.

Grading used to run in the Streamlit script thread, where nothing could interrupt a model
call that never answers. :class:`GradingJob` runs the batch on its own thread and event
loop instead (patterned on ``attendance_job.py``): the page polls :meth:`GradingJob.snapshot`
every second, and Cancel ends one student's call or all of them.

* Each student is its own task: it waits for one of ``grading_concurrency()`` slots, then
  runs under ``progress_scope`` (the model, attempt and last problem show on the page) and
  ``asyncio.timeout(student_deadline())``.
* The worker saves every finished student through the ``save`` function it is given, so a
  closed tab or an app restart loses at most the students still in flight. The module
  imports nothing from Streamlit; the page passes ``results_store.save_run``.
* Jobs live in a module-level registry keyed by a random token (``uuid4``), never by the run
  key. The page keeps the token in ``st.query_params["job"]`` and a reloaded page reattaches
  only by presenting it. Nothing lists the registry, so a session without the token cannot
  see a name, a status or a score. A finished job is dropped after ``JOB_TTL_S``.

Telemetry carries counts only (students, succeeded, failed, cancelled, timed out); never a
name, an id or a score.
"""

from __future__ import annotations

import asyncio
import re
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from cqc_cpcc.rubric_grading import AUTO_ROUTE_MODEL
from cqc_cpcc.run_locks import run_lock
from cqc_cpcc.utilities.AI import llm_limits, llm_progress
from cqc_cpcc.utilities.AI import posthog_telemetry as telemetry
from cqc_cpcc.utilities.logger import logger
from cqc_cpcc.utilities.pii_redaction import alias

QUEUED = "queued"
PREPARING = "preparing"
CALLING = "calling"
RETRYING = "retrying"
SCORING = "scoring"
DONE = "done"
NEEDS_REVIEW = "needs_review"
FAILED = "failed"
TIMED_OUT = "timed_out"
CANCELLED = "cancelled"

STATES = (QUEUED, PREPARING, CALLING, RETRYING, SCORING, DONE, NEEDS_REVIEW, FAILED, TIMED_OUT, CANCELLED)
# A student in one of these states is finished with: the job will not touch them again.
TERMINAL = (DONE, NEEDS_REVIEW, FAILED, TIMED_OUT, CANCELLED)
# Terminal states without a result: the student is listed as failed and can be re-run.
NOT_GRADED = (FAILED, TIMED_OUT, CANCELLED)
# States that hold a call to the model open (the page counts how long they have waited).
WAITING = (CALLING, RETRYING)

STATE_LABELS = {
    QUEUED: "Waiting its turn",
    PREPARING: "Preparing",
    CALLING: "Waiting for the model",
    RETRYING: "Trying again",
    SCORING: "Scoring",
    DONE: "Done",
    NEEDS_REVIEW: "Done, needs your review",
    FAILED: "Failed",
    TIMED_OUT: "Timed out",
    CANCELLED: "Cancelled",
}

# How long a finished job stays reachable by its token (the results are saved to disk too).
JOB_TTL_S = 900.0
# A call waiting longer than max(this, half its timeout) is called slow on the page.
SLOW_FLOOR_S = 60.0

SaveFn = Callable[..., None]
GradeFn = Callable[..., Awaitable[Any]]


@dataclass
class StudentProgress:
    """Where one student stands. Held under the job's lock; the page reads :class:`StudentRow`."""

    student_id: str
    state: str = QUEUED
    model: str = ""
    attempt: int = 0
    max_attempts: int = 0
    attempt_started: Optional[float] = None  # time.monotonic() when the current attempt began
    last_issue: str = ""
    score_label: str = ""
    retried: bool = False  # an attempt failed or the fallback model took over

    def waiting_s(self, now: float) -> float:
        if self.state in WAITING and self.attempt_started is not None:
            return max(0.0, now - self.attempt_started)
        return 0.0


@dataclass(frozen=True)
class StudentRow:
    student_id: str
    state: str
    status: str  # plain-language state label
    model: str
    attempt: str  # "2/3", or "" when no call is open
    waiting_s: float
    last_issue: str
    score_label: str


@dataclass(frozen=True)
class JobSnapshot:
    run_key: str
    rows: tuple[StudentRow, ...]
    total: int
    finished: int  # students in a terminal state
    counts: dict
    done: bool  # the worker has stopped
    cancel_requested: bool
    slow_after_s: float
    elapsed_s: float

    @property
    def slow(self) -> list[StudentRow]:
        return [r for r in self.rows if r.state in WAITING and r.waiting_s > self.slow_after_s]


@dataclass(frozen=True)
class JobOutcome:
    """What the page merges when the job is over (also what a mid-run save writes)."""

    results: list  # [(student_id, RubricAssessmentResult)]
    failures: list  # student ids with no result: failed, timed out, cancelled or never started
    counts: dict


def model_effort_label(model: str, effort: Optional[str]) -> str:
    return f"{model}@{effort}" if effort else model


def default_deadline(kwargs: dict) -> float:
    """One student's whole grading limit for these grading settings (both models, at the effort each runs at)."""
    auto = kwargs.get("model_name") == AUTO_ROUTE_MODEL
    return llm_limits.student_deadline(model=None if auto else (kwargs.get("model_name") or None),
                                       effort=kwargs.get("effort"), auto_route=auto)


class GradingJob:
    """Grade ``submissions`` with ``grade_kwargs`` on a background thread.

    ``grade_kwargs`` is the keyword set ``grade_with_rubric`` takes besides the submission
    (``rubric``, ``assignment_instructions``, ``reference_solution``, ``error_definitions``,
    ``model_name``, ``temperature``, ``requirements``, ``effort``). ``base_results`` and
    ``base_failures`` are the run's stored state when this job only re-grades part of it
    (a retry or a re-run with another model): the job's results merge into them.
    """

    def __init__(self, run_key: str, submissions: dict, grade_kwargs: dict, *,
                 save: Optional[SaveFn] = None, grade: Optional[GradeFn] = None,
                 deadline_s: Optional[float] = None, concurrency: Optional[int] = None,
                 base_results: Optional[list] = None, base_failures: Optional[list] = None,
                 greeting_names: Optional[dict] = None, meta: Optional[dict] = None):
        self.run_key = run_key
        self.submissions = dict(submissions)
        self.kwargs = dict(grade_kwargs)
        self.token = ""  # set by start_job
        # The run's full inputs ({"submissions", "kwargs"}) for the page to keep for retries; a
        # re-run of a few students on another model has fewer submissions and other kwargs.
        self.inputs: Optional[dict] = None
        self._save = save
        self._grade = grade
        self._deadline_s = deadline_s if deadline_s is not None else default_deadline(self.kwargs)
        self._concurrency = concurrency or llm_limits.grading_concurrency()
        self._base_results = list(base_results or [])
        self._base_failures = list(base_failures or [])
        self._greeting_names = greeting_names
        self._meta = meta
        self._request_timeout_s = llm_limits.request_timeout("grading", self.kwargs.get("effort"))
        self._lock = threading.Lock()
        self._progress = {sid: StudentProgress(sid) for sid in self.submissions}
        self._results: dict[str, Any] = {}
        self._gate_reports: dict[str, dict] = {}
        self._cancel_all = False
        self._cancel_students: set[str] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._tasks: dict[str, asyncio.Task] = {}
        self._done = threading.Event()
        self._started_at = time.monotonic()
        self._finished_at: Optional[float] = None
        self._thread = threading.Thread(target=self._run, daemon=True, name="grading-job")

    # -- page side --------------------------------------------------------------

    def start(self) -> None:
        self._started_at = time.monotonic()
        self._thread.start()

    @property
    def deadline_s(self) -> float:
        """The limit on one student's whole grading call."""
        return self._deadline_s

    @property
    def done(self) -> bool:
        return self._done.is_set()

    @property
    def finished_at(self) -> Optional[float]:
        return self._finished_at

    def wait(self, timeout: Optional[float] = None) -> bool:
        """Block until the worker has stopped (tests, and shutdown). ``True`` when it has."""
        return self._done.wait(timeout)

    def cancel(self) -> None:
        """Stop every student that has not finished. Finished students keep their results."""
        with self._lock:
            self._cancel_all = True
        self._call_in_loop(self._apply_cancels)

    def cancel_student(self, student_id: str) -> None:
        """Stop one student. Safe before the loop starts, after the student finished, and for an unknown id."""
        with self._lock:
            if student_id in self._progress:
                self._cancel_students.add(student_id)
        self._call_in_loop(self._apply_cancels)

    def snapshot(self) -> JobSnapshot:
        now = time.monotonic()
        with self._lock:
            rows = tuple(StudentRow(
                student_id=p.student_id, state=p.state, status=STATE_LABELS[p.state], model=p.model,
                attempt=f"{p.attempt}/{p.max_attempts}" if p.state in WAITING and p.attempt else "",
                waiting_s=p.waiting_s(now), last_issue=p.last_issue, score_label=p.score_label)
                for p in self._progress.values())
            counts = _count_states(self._progress)
            cancel = self._cancel_all or bool(self._cancel_students)
        end = self._finished_at if self._finished_at is not None else now
        return JobSnapshot(
            run_key=self.run_key, rows=rows, total=len(rows),
            finished=sum(counts[s] for s in TERMINAL), counts=counts, done=self._done.is_set(),
            cancel_requested=cancel, slow_after_s=max(SLOW_FLOOR_S, self._request_timeout_s / 2),
            elapsed_s=max(0.0, end - self._started_at))

    def outcome(self) -> JobOutcome:
        """The run's merged results and failures as of now (final once :attr:`done`)."""
        with self._lock:
            return self._merged_locked()

    def compile_gate_reports(self) -> dict[str, dict]:
        """Per student: what the real-compiler check did (for the page's per-model tally)."""
        with self._lock:
            return dict(self._gate_reports)

    # -- worker side ------------------------------------------------------------

    def _call_in_loop(self, fn: Callable[[], None]) -> None:
        with self._lock:
            loop = self._loop
        if loop is None or loop.is_closed():
            return  # not started yet: _main applies the flags once its tasks exist
        try:
            loop.call_soon_threadsafe(fn)
        except RuntimeError:
            pass  # the loop closed between the check and the call: the job is over

    def _apply_cancels(self) -> None:
        """Runs on the loop thread: cancel the tasks the page asked to cancel."""
        with self._lock:
            targets = list(self._tasks) if self._cancel_all else [s for s in self._cancel_students if s in self._tasks]
            tasks = [self._tasks[s] for s in targets]
        for task in tasks:
            task.cancel()

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._main(loop))
        except BaseException as e:  # noqa: BLE001 - the page must never wait on a dead worker
            logger.error(f"Grading job stopped unexpectedly: {type(e).__name__}", exc_info=True)
            with self._lock:
                for p in self._progress.values():
                    if p.state not in TERMINAL:
                        p.state, p.last_issue = FAILED, "Grading stopped unexpectedly. Run it again."
        finally:
            try:
                loop.close()
            finally:
                self._finished_at = time.monotonic()
                self._persist()
                self._done.set()

    @telemetry.tracked_run("rubric_grading")
    async def _main(self, loop: asyncio.AbstractEventLoop) -> None:
        semaphore = asyncio.Semaphore(self._concurrency)
        with self._lock:
            self._loop = loop
            for sid in self.submissions:
                self._tasks[sid] = loop.create_task(self._grade_one(sid, semaphore))
            tasks = list(self._tasks.values())
        # A cancel that arrived before the tasks existed.
        self._apply_cancels()
        await asyncio.gather(*tasks, return_exceptions=True)
        with self._lock:
            for p in self._progress.values():
                if p.state not in TERMINAL:  # cancelled before its first step ran
                    p.state, p.last_issue = CANCELLED, "Cancelled before it started."
            counts = _count_states(self._progress)
        telemetry.update_run(
            students=len(self.submissions), succeeded=counts[DONE] + counts[NEEDS_REVIEW],
            failed=counts[FAILED], cancelled=counts[CANCELLED], timed_out=counts[TIMED_OUT],
            model=self.kwargs.get("model_name"))

    def _set(self, sid: str, **fields) -> None:
        with self._lock:
            progress = self._progress[sid]
            if progress.state in TERMINAL:
                return
            for name, value in fields.items():
                setattr(progress, name, value)

    def _on_event(self, sid: str, event: llm_progress.LLMEvent) -> None:
        """An attempt began, failed, switched to the fallback or answered (loop thread)."""
        with self._lock:
            p = self._progress[sid]
            if p.state in TERMINAL:
                return
            if event.kind == "attempt_started":
                p.model, p.attempt, p.max_attempts = event.model, event.attempt, event.max_attempts
                p.attempt_started = time.monotonic()
                p.state = RETRYING if p.retried else CALLING
            elif event.kind == "attempt_failed":
                p.retried = True
                p.last_issue = (f"Attempt {event.attempt} failed: {event.error or 'no answer'}"
                                + ("; trying again" if event.will_retry else ""))
                if event.will_retry:
                    p.state = RETRYING
            elif event.kind == "fallback":
                p.retried = True
                p.last_issue = f"{event.model} did not answer; switching to the fallback model"
                p.state = RETRYING
            elif event.kind == "succeeded":
                p.model, p.state, p.attempt_started = event.model, SCORING, None

    async def _grade_one(self, sid: str, semaphore: asyncio.Semaphore) -> None:
        try:
            async with semaphore:
                await self._grade_student(sid)
        except asyncio.CancelledError:
            self._finish(sid, CANCELLED, "Cancelled by you.")
        except TimeoutError:
            minutes = self._deadline_s / 60
            logger.error(f"Grading {alias(sid)} gave no result within {minutes:.0f} min")
            self._finish(sid, TIMED_OUT, f"No result within {minutes:.0f} minutes. Re-run it with another model.")
        except Exception as e:  # noqa: BLE001 - one student's failure must not stop the others
            logger.error(f"Error grading student {alias(sid)}: {type(e).__name__}", exc_info=True)
            self._finish(sid, FAILED, _plain_issue(e))

    async def _grade_student(self, sid: str) -> None:
        from cqc_cpcc.utilities.zip_grading_utils import build_submission_text_with_token_limit

        self._set(sid, state=PREPARING)
        submission = self.submissions[sid]
        text = build_submission_text_with_token_limit(files=submission.files)
        grade = self._grade
        if grade is None:
            from cqc_cpcc.rubric_grading import grade_with_rubric as grade
        gate_report: dict = {}
        with llm_progress.progress_scope(lambda event: self._on_event(sid, event)):
            async with asyncio.timeout(self._deadline_s):
                result = await grade(
                    student_submission=text, source_files=submission.files, gate_report=gate_report,
                    rejected_files=submission.rejected_files, **self.kwargs)
        self._set(sid, state=SCORING)
        reference = self.kwargs.get("reference_solution")
        if reference:
            from cqc_cpcc.solution_similarity import solution_similarity
            result = result.model_copy(update={"solution_similarity": solution_similarity(text, reference).ratio})
        if not getattr(result, "graded_with_model", None):
            with self._lock:
                answered = self._progress[sid].model
            result = result.model_copy(update={"graded_with_model": model_effort_label(
                answered or self.kwargs.get("model_name") or "", self.kwargs.get("effort"))})
        score = f"{result.total_points_earned:g}/{result.total_points_possible:g}"
        with self._lock:
            self._results[sid] = result
            if gate_report:
                self._gate_reports[sid] = gate_report
        review = bool(getattr(result, "needs_review", False))
        self._finish(sid, NEEDS_REVIEW if review else DONE,
                     f"Needs review: {result.validity_status}" if review else "", score_label=score)
        self._persist()

    def _finish(self, sid: str, state: str, issue: str, score_label: str = "") -> None:
        with self._lock:
            p = self._progress[sid]
            if p.state in TERMINAL:
                return
            p.state, p.last_issue, p.attempt_started = state, issue or p.last_issue, None
            if score_label:
                p.score_label = score_label

    # -- saving -----------------------------------------------------------------

    def _merged_locked(self) -> JobOutcome:
        mine = set(self.submissions)
        results = [(sid, r) for sid, r in self._base_results if sid not in self._results and sid not in mine]
        results += [(sid, self._results[sid]) for sid in self.submissions if sid in self._results]
        # Anything not graded, whatever the reason, can be retried: the same rule the old
        # batch used for an interrupted run.
        failures = [sid for sid in self._base_failures if sid not in mine]
        failures += [sid for sid in self.submissions if sid not in self._results]
        return JobOutcome(results, failures, _count_states(self._progress))

    def _persist(self) -> None:
        """Save the run as it stands, under the run's lock. A failed save is logged, never raised."""
        if self._save is None:
            return
        try:
            with run_lock(self.run_key):
                outcome = self.outcome()
                self._save(self.run_key, outcome.results, outcome.failures,
                           greeting_names=self._greeting_names, meta=self._meta)
        except Exception as e:  # noqa: BLE001 - saving is a convenience; never lose the run over it
            logger.warning(f"Could not save grading results to disk: {type(e).__name__}")


def _count_states(progress: dict) -> dict:
    counts = {state: 0 for state in STATES}
    for p in progress.values():
        counts[p.state] += 1
    return counts


def _plain_issue(error: BaseException) -> str:
    """One sentence for the table. Never the exception text: it can hold submission text."""
    from cqc_cpcc.utilities.AI.openai_exceptions import OpenAISchemaValidationError, OpenAITransportError

    if isinstance(error, OpenAISchemaValidationError):
        return "The model's answer did not match the rubric format. Retry it, or use another model."
    if isinstance(error, OpenAITransportError):
        return "The model provider did not answer. Check the connection and the API key, then retry."
    return "Grading failed. Details are in the log. Retry it, or use another model."


# -- registry of running jobs ---------------------------------------------------

_TOKEN_RE = re.compile(r"[0-9a-f]{32}")
_JOBS: dict[str, GradingJob] = {}
_JOBS_GUARD = threading.Lock()


def _purge_locked(now: float) -> None:
    for token, job in list(_JOBS.items()):
        if job.finished_at is not None and now - job.finished_at > JOB_TTL_S:
            del _JOBS[token]


def start_job(job: GradingJob) -> str:
    """Register ``job`` under a new random token, start it, and return the token.

    The token is the only way back to the job. There is no listing of the registry."""
    token = uuid.uuid4().hex
    job.token = token
    with _JOBS_GUARD:
        _purge_locked(time.monotonic())
        _JOBS[token] = job
    job.start()
    return token


def attach(token: object) -> Optional[GradingJob]:
    """The job registered under ``token``, or ``None`` for a missing, malformed, wrong or expired token."""
    if not isinstance(token, str) or not _TOKEN_RE.fullmatch(token):
        return None
    with _JOBS_GUARD:
        _purge_locked(time.monotonic())
        return _JOBS.get(token)


def release(token: object) -> None:
    """Forget a job the page has merged (its results are in the session and on disk)."""
    if isinstance(token, str):
        with _JOBS_GUARD:
            _JOBS.pop(token, None)
