#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Setup tab: the effort recommendation next to the reasoning-effort control.

One short model call reads the assignment and suggests the lowest effort expected to grade
it reliably (``cqc_cpcc/effort_recommendation.py`` holds the rules). Here it runs only when
the instructor asks, on a worker thread, with a fragment that polls it and a Skip button, so
the effort control stays usable the whole time. The answer is shown with its reasons and an
Apply button. It never changes the effort on its own until the ``effort-recommendation``
suite meets the floor in ``model_policy.json``; after that it is pre-selected, and only while
the instructor has not touched the control.
"""

from __future__ import annotations

import re
from typing import Optional

import streamlit as st

from cqc_cpcc import effort_recommendation as er
from cqc_cpcc.requirement_coverage import instructions_hash
from cqc_cpcc.utilities.AI import llm_gateway, llm_limits
from cqc_streamlit_app.utils import effort_options, effort_widget_key, mark_effort_touched

STATE_KEY = "effort_recommendations"
LATEST_KEY = "effort_recommendation_latest"
JOBS_KEY = "effort_recommendation_jobs"
POLL_SECONDS = 1.0


def _plain(text: str) -> str:
    """Model text as plain characters: no Markdown or LaTeX can come out of it."""
    cleaned = re.sub(r"[`*_#>\[\]()~|\\<]", "", str(text or ""))
    return cleaned.replace("$", r"\$").strip()


def _safe(text: str) -> str:
    """Our own text (not model text): only dollar signs are escaped, so (), _ and * survive."""
    return str(text or "").replace("$", r"\$")


def _usd(value: Optional[float]) -> str:
    if value is None:
        return "a fraction of a cent"
    return rf"about \${value:.4f}" if value >= 0.0001 else "a fraction of a cent"


def preapply_for(instructions: str) -> Optional[tuple[str, str]]:
    """``(model, effort)`` to pre-select for these instructions, or ``None``.

    Only a confident answer from the judge, only once the suite meets the policy floor.
    """
    latest = st.session_state.get(LATEST_KEY, {}).get(instructions_hash(instructions or ""))
    if latest and er.policy_floor_met():
        return latest
    return None


def _remember(instructions: str, model: str, rec: er.EffortRecommendation) -> None:
    store = st.session_state.setdefault(LATEST_KEY, {})
    key = instructions_hash(instructions or "")
    if rec.source == "judge" and rec.confidence != "low" and rec.effort:
        store[key] = (model, rec.effort)
    else:
        store.pop(key, None)


def _apply(widget_uk: str, model: str, effort: str) -> None:
    """Button callback: select ``effort`` in the control (runs before the page reruns)."""
    st.session_state[effort_widget_key(widget_uk, model)] = effort
    mark_effort_touched(widget_uk, model)


def _state(key: str) -> dict:
    return st.session_state.setdefault(STATE_KEY, {}).setdefault(key, {})


@st.fragment(run_every=POLL_SECONDS)
def _running(key: str, instructions: str, model: str, timeout_s: float) -> None:
    """Poll the worker thread. Only this block reruns every second; a full rerun shows the answer."""
    state = _state(key)
    job = state.get("job")
    if job is None:
        return
    if job.finished.is_set():
        state["job"] = None
        if not job.skipped:
            state["rec"] = job.result
            if job.result is not None:
                _remember(instructions, model, job.result)
        st.rerun()
    st.caption(f"Checking how much effort this assignment needs. This takes up to {timeout_s:g} seconds "
               f"({job.elapsed_s:.0f} seconds so far). You can keep working. The Reasoning effort setting "
               "still works.")
    if st.button("Skip", key=f"skip_effort_recommendation_{key[:12]}", icon=":material/skip_next:",
                 help="Stop waiting and keep the default effort."):
        job.skip()
        state["job"] = None
        state["skipped"] = True
        st.rerun()


def _busy() -> bool:
    """True while any recommendation of this session is still running (a skipped one keeps running
    until its own bound), so Skip then Recommend again cannot start parallel paid calls."""
    jobs = st.session_state.get(JOBS_KEY, [])
    jobs[:] = [j for j in jobs if not j.finished.is_set()]
    return bool(jobs)


def render_effort_recommendation(*, widget_uk: str, model: str, use_auto_route: bool, instructions: str,
                                 checklist, solution_text: Optional[str], solution_files: int,
                                 current_effort: Optional[str]) -> None:
    """The "Recommended effort" block.

    ``current_effort`` is the effort the control gives now (``None`` = the default), so
    Apply can say when the recommendation is already selected.
    """
    st.markdown("##### Recommended effort")
    st.caption("Effort is how long the grading model thinks before it answers. Higher effort is slower and "
               "costs more. The suggestion is advice only.")
    if use_auto_route:
        st.caption("The Auto Router takes no effort setting, so there is nothing to recommend.")
        return
    if not instructions or not instructions.strip():
        st.caption("Add the instructions to get an effort recommendation.")
        return
    supported = effort_options(model)
    if not supported:
        st.caption("This model has no effort setting, so there is nothing to recommend.")
        return

    signals = er.extract_signals(instructions, checklist, solution_text, solution_files)
    default = er.default_effort_for(model)
    key = er.cache_key(instructions, signals, model, solution_text, llm_gateway.prompt_tag(er.PROMPT_ID), default)
    state = _state(key)
    floor_met = er.policy_floor_met()
    st.caption(f"What the app counted in your instructions: {_safe(signals.summary())}.")
    if not signals.solution_files:
        st.caption("No solution file is added, so the suggestion will not go below medium effort.")

    if state.get("job") is not None:
        _running(key, instructions, model, llm_limits.effort_judge_timeout())
        return

    rec: Optional[er.EffortRecommendation] = state.get("rec")
    if rec is None and key in er._RECOMMENDATION_CACHE:
        rec = state["rec"] = er._RECOMMENDATION_CACHE[key]
        _remember(instructions, model, rec)

    if rec is None:
        if state.get("skipped"):
            st.caption(f"Skipped. The default effort ({_plain(default or 'the provider default')}) stays.")
        estimate = er.estimate_cost(instructions, solution_text, model)
        st.caption(f"Estimated cost: {_usd(estimate)}, for one short request to the grading model. The answer "
                   "is remembered for these instructions and this model.")
        st.caption("Sends the instructions and your solution (no student work) to the model provider.")
        busy = _busy()
        if busy:
            st.caption("The button is off because an earlier request is still running. It ends by itself "
                       f"within {llm_limits.effort_judge_timeout():g} seconds.")
        if st.button("Recommend effort", key=f"recommend_effort_{key[:12]}", icon=":material/lightbulb:",
                     disabled=busy,
                     help="Ask the grading model which effort this assignment needs. Nothing changes until you "
                          "press Apply."):
            state["skipped"] = False
            job = er.EffortJob(instructions, signals, model, solution_text)
            st.session_state.setdefault(JOBS_KEY, []).append(job)
            state["job"] = job
            st.rerun()
        return

    if rec.source == "default" or not rec.effort:
        st.caption(f"{_plain(rec.note or 'No recommendation.')} Default effort: "
                   f"{_plain(default or 'the provider default')}.")
    else:
        st.markdown(f"Recommended effort: **{_plain(rec.effort)}**.")
        if rec.reasons:
            st.caption("Why:\n" + "\n".join(f"- {_plain(r)}" for r in rec.reasons))
        _apply_controls(widget_uk, model, rec, current_effort or default, floor_met, key)
    if rec.cost_usd:
        st.caption(rf"Cost of this suggestion: about \${rec.cost_usd:.4f}.")
    if st.button("Recommend again", key=f"recommend_again_{key[:12]}", icon=":material/refresh:",
                 disabled=_busy(), help="Ask again and replace the saved answer."):
        er._RECOMMENDATION_CACHE.pop(key, None)
        state.clear()
        st.rerun()


def _apply_controls(widget_uk: str, model: str, rec: er.EffortRecommendation, current: Optional[str],
                    floor_met: bool, key: str) -> None:
    same = current == rec.effort
    st.caption("This is a suggestion for this assignment. The effort you choose is used for every student in "
               "this run, so grade the whole section at one effort.")
    if er._rank(rec.effort) < er._rank(rec.default_effort):
        st.caption("Lower effort costs less; check a few results before returning grades.")
    st.button(f"Apply {rec.effort} effort", key=f"apply_effort_{key[:12]}", icon=":material/check:",
              on_click=_apply, args=(widget_uk, model, rec.effort), disabled=same,
              help="Select this effort in the Reasoning effort setting.")
    if same:
        st.caption("Apply is off because this effort is already selected.")
    elif not floor_met:
        st.caption("This is a suggestion only. Nothing is selected for you. Press Apply to use it.")
    st.caption("Apply sets the Reasoning effort setting for this run. Every student in the run is graded at "
               "that one effort, so the grades stay comparable.")
