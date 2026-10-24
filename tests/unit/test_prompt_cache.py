#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Prompt-cache layout, the optional session id, and the cache usage columns of the eval records."""

import json

import pytest

from cqc_cpcc import prompt_cache
from cqc_cpcc.grading_diagnostics import find_rubric
from cqc_cpcc.model_eval import prompt_automation as pa
from cqc_cpcc.model_eval.suites import base
from cqc_cpcc.rubric_grading import build_rubric_grading_messages
from cqc_cpcc.utilities.AI.exam_grading_prompts import build_exam_grading_messages

pytestmark = pytest.mark.unit

RUBRIC = find_rubric("csc_java_exam_rubric")
INSTRUCTIONS = "Write OrderTotal. " * 40
SOLUTION = "public class OrderTotal { /* reference */ }"
STUDENT_A = "### File: A.java\n```java\nclass A { int a; }\n```"
STUDENT_B = "### File: B.java\n```java\nclass B { String b = \"## Student Submission to Grade\"; }\n```"

CASES = {
    "rubric": (lambda sub: build_rubric_grading_messages(RUBRIC, INSTRUCTIONS, sub, reference_solution=SOLUTION),
               prompt_cache.RUBRIC_SUBMISSION_HEADING),
    "exam": (lambda sub: build_exam_grading_messages(INSTRUCTIONS, SOLUTION, sub, ["Major A"], ["minor b"]),
             prompt_cache.EXAM_SUBMISSION_HEADING),
}


@pytest.mark.parametrize("kind", CASES)
def test_two_students_of_one_assignment_share_a_byte_identical_prefix(kind, capsys):
    build, heading = CASES[kind]
    (sys_a, user_a), (sys_b, user_b) = build(STUDENT_A), build(STUDENT_B)
    pre_a, pre_b = prompt_cache.static_prefix(sys_a, user_a, heading), prompt_cache.static_prefix(sys_b, user_b, heading)
    assert pre_a is not None and pre_a == pre_b
    # Everything after the prefix is the per-call part: the nonce, the fence and the submission.
    total_a = len((sys_a + "\n\n" + user_a).encode())
    assert user_a.endswith("Grade the fenced submission per the system rules.")
    assert STUDENT_A.split("\n")[0] not in pre_a and "A.java" not in pre_a and "B.java" not in pre_b
    # The numbers below go in the PR body (an estimate: about 4 bytes per token for English and code).
    print(f"{kind}: shared prefix {len(pre_a.encode())} of {total_a} bytes "
          f"({len(pre_a.encode()) / total_a:.0%}); estimate about {len(pre_a.encode()) // 4} tokens")
    assert len(pre_a.encode()) / total_a > 0.5


@pytest.mark.parametrize("kind", CASES)
def test_a_student_cannot_move_the_cut_with_a_forged_heading(kind):
    build, heading = CASES[kind]
    (sys_a, user_a), (sys_b, user_b) = build(STUDENT_A), build(STUDENT_B)  # B quotes the heading in its code
    assert prompt_cache.static_prefix(sys_a, user_a, heading) == prompt_cache.static_prefix(sys_b, user_b, heading)
    assert "String b" not in prompt_cache.static_prefix(sys_b, user_b, heading)


@pytest.mark.parametrize("kind", CASES)
def test_the_session_id_is_off_by_default_and_a_prefix_hash_when_on(kind, monkeypatch):
    build, heading = CASES[kind]
    (sys_a, user_a), (sys_b, user_b) = build(STUDENT_A), build(STUDENT_B)
    monkeypatch.delenv("CQC_PROMPT_CACHE_KEY", raising=False)
    assert prompt_cache.session_id_for(sys_a, user_a, heading, "m", "high") is None
    monkeypatch.setenv("CQC_PROMPT_CACHE_KEY", "on")
    key_a = prompt_cache.session_id_for(sys_a, user_a, heading, "m", "high")
    key_b = prompt_cache.session_id_for(sys_b, user_b, heading, "m", "high")
    assert key_a == key_b and key_a.startswith("cqc-") and len(key_a) <= 256
    assert "A.java" not in key_a and "Student" not in key_a
    assert key_a != prompt_cache.session_id_for(sys_a, user_a, heading, "other-model", "high")
    other = build_rubric_grading_messages(RUBRIC, "A different assignment", STUDENT_A)
    assert prompt_cache.session_id_for(*other, prompt_cache.RUBRIC_SUBMISSION_HEADING, "m", "high") != (
        prompt_cache.session_id_for(*CASES["rubric"][0](STUDENT_A), prompt_cache.RUBRIC_SUBMISSION_HEADING, "m", "high"))


def test_no_prefix_found_means_no_session_id(monkeypatch):
    monkeypatch.setenv("CQC_PROMPT_CACHE_KEY", "on")
    assert prompt_cache.session_id_for("system", "a user message with no fence", "## X") is None


def test_the_session_id_is_deterministic_under_request_capture(monkeypatch):
    from cqc_cpcc.model_eval import request_capture

    monkeypatch.setenv("CQC_PROMPT_CACHE_KEY", "on")
    with request_capture._patched([], test_mode=True):
        ids = {prompt_cache.session_id_for(*build_rubric_grading_messages(RUBRIC, "i", "s"),
                                           prompt_cache.RUBRIC_SUBMISSION_HEADING, "m", "high") for _ in range(3)}
    assert len(ids) == 1


# --- eval records -------------------------------------------------------------------------------

def _rec(**kw):
    return base.SuiteRecord(suite="s", case_id=kw.pop("case_id", "c"), model="m", effort="high", repeat=0,
                            ok=True, cost_usd=kw.pop("cost_usd", 0.01), **kw)


def test_the_new_usage_keys_are_additive_and_old_rows_still_load():
    old = json.loads(_rec(prompt_tokens=1000).to_json())
    assert "cached_tokens" not in old and "reasoning_tokens" not in old and "cache_write_tokens" not in old
    new = json.loads(_rec(prompt_tokens=1000, cached_tokens=800, reasoning_tokens=300, cache_write_tokens=100).to_json())
    assert (new["cached_tokens"], new["reasoning_tokens"], new["cache_write_tokens"]) == (800, 300, 100)
    again = base.SuiteRecord.from_json(json.dumps(new))
    assert again.cached_tokens == 800
    assert base.SuiteRecord.from_json(json.dumps(old)).cached_tokens is None


def test_cache_stats_report_the_cached_share_and_cost_per_call():
    rows = [_rec(prompt_tokens=1000, cached_tokens=0, reasoning_tokens=100, cost_usd=0.02, case_id="a"),
            _rec(prompt_tokens=1000, cached_tokens=900, reasoning_tokens=200, cost_usd=0.01, case_id="b")]
    stats = base.cache_stats(rows)
    assert stats["prompt_tokens"] == 2000 and stats["cached_tokens"] == 900 and stats["cached_share"] == 0.45
    assert stats["reasoning_tokens"] == 300 and stats["cost_per_call_usd"] == pytest.approx(0.015)
    assert base.cache_stats([])["cached_share"] == 0.0


def test_run_stats_markdown_lists_head_and_base_cache_use():
    head = base.cache_stats([_rec(prompt_tokens=1000, cached_tokens=800, reasoning_tokens=5)])
    basest = base.cache_stats([_rec(prompt_tokens=1000)])
    report = {"suites": {"grading": {"run_stats": {"elapsed_s": 1, "head_cache": head, "base_cache": basest}}},
              "wall_clock_s": 1.0}
    text = "\n".join(pa._run_stats_markdown(report))
    assert "Prompt cache (report-only" in text and "80% of 1000 prompt tokens cached" in text
    assert "base: 0% of 1000" in text
