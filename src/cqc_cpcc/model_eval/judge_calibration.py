#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Calibrate the LLM judges against human labels.

* Gold sets (``evals/judges/calibration/<judge>.jsonl``): 40 synthetic outputs per judge,
  built here at four intended quality levels (``constructed_score`` 1-4) across both
  courses. Regenerate with ``python -m cqc_cpcc.model_eval build-judge-gold``.
* Human labels (``<judge>.labels.json``): written by ``judge-label`` (Christopher, about
  30 minutes per judge). The constructed score is only a draft; calibration uses the
  human label.
* ``judge-calibrate`` runs the judge on every labelled item and writes
  ``<judge>.report.json``: quadratic-weighted kappa with a bootstrap 95% interval and
  within-one agreement. A judge counts in scores only when the report matches the pinned
  judge model and prompt fingerprint and the lower kappa bound and within-one agreement
  meet ``model_policy.json`` (``prompt_eval.judge``).
"""

from __future__ import annotations

import json
import random
import statistics
from pathlib import Path
from typing import Optional

from cqc_cpcc.model_eval import dataset_builder as db
from cqc_cpcc.model_eval.judges import CALIBRATION_DIR, CRITERIA, JudgeVerdict, _ask, build_judge_prompt

GOLD_JUDGES = ("feedback-quality", "faithfulness")

#: (assignment key, mutation key) -> (specific + correct + actionable, correct but generic,
#: invented problem, invented requirement)
MUTATION_TEXT = {
    ("csc151_exam1_java", "boundary"): (
        "The bulk discount check uses `quantity > BULK_QUANTITY`, so an order of exactly 10 items gets no "
        "discount even though the instructions say 10 or more. Change the comparison to `>=` and test with a "
        "quantity of 10.",
        "The discount condition is not quite right. Re-read the instructions for when the discount applies.",
        "The program never closes the Scanner, which leaks memory on every run.",
        "The program should also print the date of the order on the receipt."),
    ("csc151_exam1_java", "tax_dropped"): (
        "`total` is set to `subtotal`, so the 7% tax you compute in `tax` is never added: the Total line "
        "prints the pre-tax amount. Set `total = subtotal + tax`.",
        "The total is wrong. Check the tax step.",
        "The subtotal multiplies the price by the quantity twice.",
        "Totals over $100 should be rounded to the nearest dollar."),
    ("csc151_exam1_java", "magic_tax"): (
        "The tax rate is written as the literal `0.07` inside the calculation instead of the named constant "
        "`TAX_RATE` the instructions ask for. Declare `TAX_RATE` once at the top and use it in the calculation.",
        "Use named constants where the instructions ask for them.",
        "The discount is applied after the tax instead of before it.",
        "Constants must be loaded from a configuration file."),
    ("csc151_exam1_java", "snake_case"): (
        "`Item_Price` does not follow Java naming conventions; local variables use camelCase, so rename it "
        "`itemPrice` everywhere it appears.",
        "Some names do not follow Java conventions.",
        "The class name `OrderTotal` should be all lowercase.",
        "Every variable name must be at least 12 characters long."),
    ("csc151_exam1_java", "println_total"): (
        "The Total line uses `println(\"Total: $\" + total)`, which prints many decimal places (for example "
        "$53.499999). Use `printf(\"Total: $%.2f%n\", total)` like the other two lines.",
        "The output formatting is not consistent.",
        "The Subtotal line is missing from the output.",
        "Output must also be written to a text file."),
    ("csc134_project_cpp", "missing_multiplier"): (
        "In `calculatePay`, overtime hours are multiplied by `rate` but not by `OVERTIME_MULTIPLIER`, so 45 "
        "hours at $10 pays $450 instead of $475. Multiply the overtime part by `OVERTIME_MULTIPLIER`.",
        "The overtime pay calculation is incorrect.",
        "The program does not validate that the hourly rate is positive.",
        "Overtime should be paid at double time on weekends."),
    ("csc134_project_cpp", "flipped_overtime"): (
        "The overtime test is `hours < OVERTIME_THRESHOLD`, which is reversed: 45 hours takes the regular-pay "
        "branch and 30 hours takes the overtime branch. Use `hours > OVERTIME_THRESHOLD`.",
        "Check the condition that decides when overtime applies.",
        "`calculatePay` is declared but never called from main.",
        "The program must support salaried employees too."),
    ("csc134_project_cpp", "no_validation"): (
        "Hours and rate are read once and used as typed, so -5 hours or a rate of 0 are accepted. Add the "
        "`while` loops the instructions ask for to re-prompt until hours are 0-80 and the rate is above 0.",
        "Input is not validated.",
        "The gross pay is printed without a dollar sign.",
        "The program must log every invalid entry to a file."),
    ("csc134_project_cpp", "label_changed"): (
        "The output reads `pay=475` instead of the required `Gross pay: $475.00`, so it will not match the "
        "expected output. Print exactly `Gross pay: $` followed by the value.",
        "The output does not match the instructions.",
        "The program uses `endl` instead of `\\n`, which is an error.",
        "The output must be centered in an 80-column box."),
    ("csc134_project_cpp", "int_rate"): (
        "`hourlyRate` is declared as `int`, so a rate of 15.75 is read as 15 and pay is too low. Declare it "
        "`double` as the instructions say.",
        "One of the variables has the wrong data type.",
        "`hoursWorked` should be an `int` because hours are whole numbers.",
        "Rates must be stored in cents as a `long`."),
}

_HARSH_LEAK = ("This is wrong and careless. Here is the corrected program, copy it:\n```\n" + "// fixed line\n" * 12
               + "```")


def _case_context(a: db.Assignment, source: str) -> str:
    return f"{a.instructions}\n\n## Student work\nFile: {a.filename}\n{source}"


def _feedback_items() -> list[dict]:
    items = []
    cases = {c.case_id: c for c in db.build_cases()}
    for (akey, mkey), (specific, generic, invented, _req) in MUTATION_TEXT.items():
        case = cases[f"{akey}__{mkey}"]
        context = _case_context(case.assignment, case.source)
        levels = {
            4: specific + " Your input handling is clear and well commented.",
            3: generic,
            2: "Some parts of the program could be better. Review the instructions and try again.",
            1: invented + " " + _HARSH_LEAK,
        }
        for score, text in levels.items():
            items.append({"item_id": f"{case.case_id}__fq{score}", "judge": "feedback-quality",
                          "case_id": case.case_id, "context": context, "output": text, "constructed_score": score})
    return items


def _faithfulness_items() -> list[dict]:
    items = []
    cases = {c.case_id: c for c in db.build_cases()}
    for (akey, mkey), (specific, generic, invented, requirement) in MUTATION_TEXT.items():
        case = cases[f"{akey}__{mkey}"]
        context = _case_context(case.assignment, case.source)
        levels = {
            4: specific,
            3: generic,  # true but unspecific: nothing invented, little supported detail
            2: specific + " Also: " + invented,
            1: invented + " " + requirement,
        }
        for score, text in levels.items():
            items.append({"item_id": f"{case.case_id}__fa{score}", "judge": "faithfulness",
                          "case_id": case.case_id, "context": context, "output": text, "constructed_score": score})
    return items


BUILDERS = {"feedback-quality": _feedback_items, "faithfulness": _faithfulness_items}


def gold_path(judge_id: str) -> Path:
    return CALIBRATION_DIR / f"{judge_id}.jsonl"


def labels_path(judge_id: str) -> Path:
    return CALIBRATION_DIR / f"{judge_id}.labels.json"


def render_gold(judge_id: str) -> str:
    return "".join(json.dumps(i, ensure_ascii=False, sort_keys=True) + "\n" for i in BUILDERS[judge_id]())


def write_gold() -> dict:
    CALIBRATION_DIR.mkdir(parents=True, exist_ok=True)
    counts = {}
    for judge_id in GOLD_JUDGES:
        text = render_gold(judge_id)
        gold_path(judge_id).write_text(text, encoding="utf-8")
        counts[judge_id] = text.count("\n")
    return counts


def load_gold(judge_id: str) -> list[dict]:
    return [json.loads(line) for line in gold_path(judge_id).read_text(encoding="utf-8").splitlines() if line]


def load_labels(judge_id: str) -> dict:
    path = labels_path(judge_id)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"labeled_by": None, "labels": {}}


# --- Agreement ---------------------------------------------------------------------------

def weighted_kappa(a: list[int], b: list[int], categories=(1, 2, 3, 4)) -> Optional[float]:
    """Quadratic-weighted Cohen's kappa of two raters' integer scores."""
    if not a or len(a) != len(b):
        return None
    k = len(categories)
    index = {c: i for i, c in enumerate(categories)}
    observed = [[0.0] * k for _ in range(k)]
    for x, y in zip(a, b):
        observed[index[x]][index[y]] += 1
    n = len(a)
    row = [sum(r) for r in observed]
    col = [sum(observed[i][j] for i in range(k)) for j in range(k)]
    num = den = 0.0
    for i in range(k):
        for j in range(k):
            w = (i - j) ** 2 / (k - 1) ** 2
            num += w * observed[i][j] / n
            den += w * row[i] * col[j] / (n * n)
    return 1.0 - num / den if den else 1.0


def agreement(judge: list[int], human: list[int], resamples: int = 2000, seed: int = 0) -> dict:
    pairs = list(zip(judge, human))
    rng = random.Random(seed)
    kappas = []
    for _ in range(resamples):
        sample = [rng.choice(pairs) for _ in pairs]
        value = weighted_kappa([s[0] for s in sample], [s[1] for s in sample])
        if value is not None:
            kappas.append(value)
    kappas.sort()
    return {
        "n": len(pairs),
        "kappa": weighted_kappa(judge, human),
        "kappa_low": kappas[int(0.025 * (len(kappas) - 1))] if kappas else None,
        "kappa_high": kappas[int(0.975 * (len(kappas) - 1))] if kappas else None,
        "within_one": statistics.fmean(1.0 if abs(x - y) <= 1 else 0.0 for x, y in pairs) if pairs else None,
        "exact": statistics.fmean(1.0 if x == y else 0.0 for x, y in pairs) if pairs else None,
    }


def to_level(verdict: JudgeVerdict, judge_id: str) -> int:
    wanted = set(CRITERIA[judge_id])
    scores = [min(4, max(1, s.score)) for s in verdict.scores if s.criterion in wanted] or [1]
    return int(round(statistics.fmean(scores)))


async def calibrate(judge_id: str, judge_model: str, use_constructed: bool = False) -> dict:
    """Run the judge on the gold set and compare with human labels (or constructed scores,
    which gives a provisional report that never counts)."""
    from cqc_cpcc.model_eval import prompt_registry

    items = load_gold(judge_id)
    labels = load_labels(judge_id)
    human = {} if use_constructed else labels["labels"]
    rows = [(i, human.get(i["item_id"], i["constructed_score"] if use_constructed else None)) for i in items]
    rows = [(i, h) for i, h in rows if h is not None]
    if len(rows) < 40 and not use_constructed:
        raise SystemExit(f"{judge_id}: only {len(rows)} human labels; run `judge-label --judge {judge_id}` first")
    judged, spent = [], 0.0
    for item, label in rows:
        prompt = build_judge_prompt(judge_id, context=item["context"], output=item["output"])
        verdict, cost = await _ask(judge_id, prompt, JudgeVerdict, judge_model)
        spent += cost
        judged.append((to_level(verdict, judge_id), int(label)))
    stats = agreement([j for j, _ in judged], [h for _, h in judged])
    entry = prompt_registry.load().prompts[f"judge-{judge_id}"]
    report = {"judge_id": judge_id, "judge_model": judge_model, "judge_fingerprint": entry.fingerprint,
              "judge_version": entry.version, "labels": "constructed (provisional)" if use_constructed
              else f"human ({labels.get('labeled_by')})", "spent_usd": round(spent, 6), **stats}
    if use_constructed:
        report["kappa_low"] = None  # a provisional report never makes a judge count
    return report


def label_interactively(judge_id: str, labeled_by: str, inp=input, out=print) -> int:
    """Show each unlabelled item and record a 1-4 score. Returns how many were labelled."""
    data = load_labels(judge_id)
    data["labeled_by"] = labeled_by
    criteria = ", ".join(CRITERIA[judge_id])
    done = 0
    for item in load_gold(judge_id):
        if item["item_id"] in data["labels"]:
            continue
        out(f"\n=== {item['item_id']} ({judge_id}: {criteria}) ===\n{item['context'][:3000]}\n"
            f"--- OUTPUT ---\n{item['output']}\n")
        answer = ""
        while answer not in ("1", "2", "3", "4", "q"):
            answer = inp("Overall 1-4 (q to stop): ").strip().lower()
        if answer == "q":
            break
        data["labels"][item["item_id"]] = int(answer)
        labels_path(judge_id).write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        done += 1
    return done
