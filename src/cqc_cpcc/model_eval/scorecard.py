#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Public-safe scorecard (JSON + Markdown) for one evaluation run.

Contains only synthetic case ids and aggregate numbers: no prompts, no model output,
and no free text copied from OpenRouter's model catalogue.
"""

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path

from cqc_cpcc.model_eval.gates import Decision

AGGREGATE_KEYS = (
    "composite", "f1", "precision", "recall", "score_accuracy", "jaccard", "score_std",
    "ok_rate", "invalid_ids", "injection_pass_rate", "scorable_cases", "cost_usd",
    "cost_per_submission", "latency_p50_s", "latency_p95_s", "attempted", "skipped_budget",
)


def build(meta: dict, aggregates: dict, decisions: dict[str, Decision], winner) -> dict:
    return {
        **meta,
        "models": {
            model: {
                **{k: agg.get(k) for k in AGGREGATE_KEYS},
                "errors": agg.get("errors", {}),
                "per_language": agg.get("per_language", {}),
                "per_case": [
                    {"case_id": m.case_id, "composite": m.composite, "f1": m.f1,
                     "score_accuracy": m.score_accuracy, "ok_calls": m.ok_calls, "calls": m.calls}
                    for m in agg.get("per_case", [])
                ],
            }
            for model, agg in aggregates.items()
        },
        "decisions": {model: dataclasses.asdict(d) for model, d in decisions.items()},
        "winner": winner,
    }


def write(out_dir: Path, scorecard: dict) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "scorecard.json"
    json_path.write_text(json.dumps(scorecard, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    md_path = out_dir / "scorecard.md"
    md_path.write_text(to_markdown(scorecard), encoding="utf-8")
    return json_path, md_path


def _fmt(value, digits=3) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _safe(text: str) -> str:
    """Neutralise @mentions and markdown links in anything echoed into a PR body."""
    zwsp = "\u200b"
    text = re.sub(r"(^|[\s(])@(?=\w)", lambda m: m.group(1) + "@" + zwsp, str(text))
    return text.replace("](", "]" + zwsp + "(")


def to_markdown(sc: dict) -> str:
    lines = [
        f"## Model evaluation {_safe(sc.get('run_label', ''))}",
        "",
        f"Dataset `{sc.get('dataset')}` ({sc.get('cases')} cases x {sc.get('repeats')} repeats), "
        f"incumbent `{_safe(sc.get('incumbent'))}`, spent ${_fmt(sc.get('spent_usd'), 2)} "
        f"of ${_fmt(sc.get('budget_usd'), 2)}. Status: **{sc.get('status')}**.",
        "",
        "| Model | Effort | Composite | F1 | Score acc. | Jaccard | OK rate | Injection | $/submission | p95 s |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    efforts = sc.get("efforts", {})
    for model, m in sc["models"].items():
        lines.append(
            f"| `{_safe(model)}` | {_safe(efforts.get(model) or 'default')} | {_fmt(m['composite'])} | "
            f"{_fmt(m['f1'])} | {_fmt(m['score_accuracy'])} | {_fmt(m['jaccard'])} | {_fmt(m['ok_rate'])} | "
            f"{_fmt(m['injection_pass_rate'])} | {_fmt(m['cost_per_submission'], 5)} | "
            f"{_fmt(m['latency_p95_s'], 1)} |"
        )
    lines += ["", "### Decisions", ""]
    for model, d in sc["decisions"].items():
        verdict = f"eligible via **{d['path']}** path" if d["eligible"] else "not eligible"
        lines.append(f"- `{_safe(model)}`: {verdict}.")
        for reason in d["reasons"]:
            lines.append(f"  - {_safe(reason)}")
    lines += ["", f"**Winner:** `{_safe(sc.get('winner'))}`" if sc.get("winner") else "**Winner:** none (incumbent stays)", ""]
    return "\n".join(lines) + "\n"
