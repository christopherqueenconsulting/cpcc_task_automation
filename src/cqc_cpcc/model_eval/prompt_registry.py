#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Track every LLM prompt: its id, version and a fingerprint of what is sent.

``src/cqc_cpcc/config/prompt_registry.json`` lists each prompt (human-owned, like
``model_policy.json``). Each prompt here has one or more *renders*: the real builder
called with fixed synthetic inputs, so a change to the prompt text (in any branch the
renders reach) changes the output. The **fingerprint** is a SHA-256 over the request
built from those renders exactly as the gateway sends it (messages + strict
``json_schema`` response format, via ``openrouter_client.build_messages`` /
``build_response_format``). Model-dependent request params (model, reasoning effort,
token limit) are not part of it: they belong to the model registry.

A fingerprint that no longer matches the manifest fails ``test_prompt_registry``. Fix:
bump the prompt's ``version`` and run ``python -m cqc_cpcc.model_eval prompts fingerprint
--write`` (see ``docs/PROMPTS.md``). ``prompts check --base-ref <ref>`` verifies that every
changed fingerprint came with a version bump.

No real student work: every input below is synthetic.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Callable, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

REPO_ROOT = Path(__file__).resolve().parents[3]
REGISTRY_PATH = REPO_ROOT / "src" / "cqc_cpcc" / "config" / "prompt_registry.json"
REGISTRY_RELPATH = "src/cqc_cpcc/config/prompt_registry.json"

Status = Literal["live", "fragment", "dead-path", "judge"]


class PromptEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    version: int = Field(ge=1)
    status: Status
    role: Optional[Literal["grading", "digest", "feedback", "flowgorithm"]] = None
    description: str
    builder: str  # "module:attr" of the function or constant that produces the text
    schema_model: Optional[str] = Field(default=None, alias="schema")  # "module:Class"; None = free text
    parent: Optional[str] = None  # for fragments: the prompt that embeds this one
    call_sites: list[str] = Field(default_factory=list)  # "module:function" that send it
    source_files: list[str] = Field(default_factory=list)
    shared_files: list[str] = Field(default_factory=list)
    suite: Optional[str] = None
    graders: list[str] = Field(default_factory=list)
    fingerprint: str


class PromptRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int
    owner_note: str = Field(alias="_owner")
    prompts: dict[str, PromptEntry]


def load(path: Path = REGISTRY_PATH) -> PromptRegistry:
    return PromptRegistry.model_validate_json(path.read_text(encoding="utf-8"))


def import_ref(ref: str):
    """``"pkg.module:Attr.attr"`` -> the object."""
    module_name, _, attr_path = ref.partition(":")
    obj = importlib.import_module(module_name)
    for part in attr_path.split("."):
        obj = getattr(obj, part)
    return obj


# --- Canonical synthetic inputs ------------------------------------------------------

FIXTURE_INSTRUCTIONS = """Write a Java program named OrderTotal.java that:
1. Asks the user for the number of items and the price per item.
2. Computes the subtotal, adds 7% sales tax, and prints the total with two decimals.
3. Prints "Invalid input" and exits when either value is negative.
Use named constants for the tax rate and comment your code."""

FIXTURE_SOLUTION = """import java.util.Scanner;

public class OrderTotal {
    static final double TAX_RATE = 0.07;

    public static void main(String[] args) {
        Scanner in = new Scanner(System.in);
        int items = in.nextInt();
        double price = in.nextDouble();
        if (items < 0 || price < 0) {
            System.out.println("Invalid input");
            return;
        }
        double subtotal = items * price;
        System.out.printf("Total: %.2f%n", subtotal * (1 + TAX_RATE));
    }
}"""

FIXTURE_SUBMISSION = """File: OrderTotal.java
```java
import java.util.Scanner;

public class OrderTotal {
    public static void main(String[] args) {
        Scanner in = new Scanner(System.in);
        int items = in.nextInt();
        double price = in.nextDouble();
        double total = items * price * 1.07;
        System.out.println("Total: " + total);
    }
}
```"""

FIXTURE_COURSE = "CSC 151"
FIXTURE_FILE_NAME = "OrderTotal.fprg"
FIXTURE_RUBRIC_TABLE = """| Criterion | Points |
|---|---|
| Program produces correct output | 40 |
| Input validation | 20 |
| Named constants | 20 |
| Comments | 20 |"""
FIXTURE_TOTAL_POINTS = "100"
FIXTURE_MAJOR_TYPES = ["CSC_151_EXAM_1_SYNTAX_ERROR - code does not compile",
                       "CSC_151_EXAM_1_MISSING_REQUIREMENT - a required behavior is missing"]
FIXTURE_MINOR_TYPES = ["CSC_151_EXAM_1_NAMING_CONVENTIONS - names do not follow Java conventions"]
FIXTURE_FEEDBACK_TYPES = ["COMMENTS_MISSING - the code is not commented",
                          "INPUT_VALIDATION - invalid input is not handled"]


def _fixture_rubric(scoring_mode: str):
    from cqc_cpcc.rubric_models import (
        Criterion,
        ErrorCountScoringRules,
        OverallBand,
        PerformanceLevel,
        Rubric,
    )

    levels = [
        PerformanceLevel(label="Proficient", score_min=40, score_max=50,
                         description="Meets every requirement"),
        PerformanceLevel(label="Developing", score_min=20, score_max=39,
                         description="Some requirements missing"),
        PerformanceLevel(label="Beginning", score_min=0, score_max=19,
                         description="Most requirements missing"),
    ]
    criteria = [
        Criterion(criterion_id="functionality", name="Functionality", max_points=50,
                  description="The program computes the right total", levels=levels,
                  scoring_mode=scoring_mode,
                  error_rules=(ErrorCountScoringRules(major_weight=10, minor_weight=2)
                               if scoring_mode == "error_count" else None)),
        Criterion(criterion_id="style", name="Style and documentation", max_points=50,
                  description="Constants, naming and comments", levels=levels,
                  scoring_mode="manual"),
    ]
    bands = [OverallBand(label="Pass", score_min=60, score_max=100),
             OverallBand(label="Not yet", score_min=0, score_max=59)]
    return Rubric(rubric_id="fixture_rubric", rubric_version="1", title="Order Total Rubric",
                  description="Synthetic fixture rubric", criteria=criteria, overall_bands=bands)


def _fixture_error_definitions():
    from cqc_cpcc.error_definitions_models import ErrorDefinition

    return [
        ErrorDefinition(error_id="FIXTURE_MISSING_VALIDATION", name="Missing validation",
                        description="Negative input is not rejected", severity_category="major",
                        examples=["no check for items < 0"]),
        ErrorDefinition(error_id="FIXTURE_MAGIC_NUMBER", name="Magic number",
                        description="A literal is used instead of a named constant",
                        severity_category="minor", examples=["* 1.07"]),
        ErrorDefinition(error_id="FIXTURE_DISABLED", name="Disabled",
                        description="Disabled errors must not appear", severity_category="minor",
                        enabled=False),
    ]


def _fixture_checklist():
    from cqc_cpcc.requirement_coverage import RequirementChecklist

    return RequirementChecklist.model_validate({"requirements": [
        {"id": "R1", "text": "Reads the item count and price", "weight": "core"},
        {"id": "R2", "text": "Prints the total with 7% tax and two decimals", "weight": "core"},
        {"id": "R3", "text": "Rejects negative input with 'Invalid input'", "weight": "secondary"},
    ]})


# --- Renders: the real builder on the fixtures ---------------------------------------

def _render_rubric_grading() -> list[str]:
    from cqc_cpcc.rubric_grading import build_rubric_grading_prompt

    return [
        # error-count rubric with error definitions, checklist and reference solution
        build_rubric_grading_prompt(_fixture_rubric("error_count"), FIXTURE_INSTRUCTIONS,
                                    FIXTURE_SUBMISSION, reference_solution=FIXTURE_SOLUTION,
                                    error_definitions=_fixture_error_definitions(),
                                    requirements=_fixture_checklist()),
        # level-band rubric, no error definitions, no checklist
        build_rubric_grading_prompt(_fixture_rubric("level_band"), FIXTURE_INSTRUCTIONS,
                                    FIXTURE_SUBMISSION),
    ]


def _render_requirements_section() -> list[str]:
    from cqc_cpcc.requirement_coverage import requirements_prompt_section

    return ["\n".join(requirements_prompt_section(_fixture_checklist()))]


def _render_requirement_extraction() -> list[str]:
    from cqc_cpcc.requirement_coverage import build_requirement_extraction_prompt

    return [build_requirement_extraction_prompt(FIXTURE_INSTRUCTIONS)]


def _render_exam_grading() -> list[str]:
    from cqc_cpcc.utilities.AI.exam_grading_prompts import build_exam_grading_prompt

    return [build_exam_grading_prompt(FIXTURE_INSTRUCTIONS, FIXTURE_SOLUTION, FIXTURE_SUBMISSION,
                                      FIXTURE_MAJOR_TYPES, FIXTURE_MINOR_TYPES)]


def _render_preprocessing_digest() -> list[str]:
    from cqc_cpcc.utilities.AI.openai_client import _build_preprocessing_prompt

    return [
        _build_preprocessing_prompt(FIXTURE_SUBMISSION, FIXTURE_INSTRUCTIONS,
                                    rubric_config=FIXTURE_RUBRIC_TABLE),
        _build_preprocessing_prompt(FIXTURE_SUBMISSION, FIXTURE_INSTRUCTIONS),
    ]


def _render_project_feedback() -> list[str]:
    from cqc_cpcc.prompts.project_feedback import CODE_ASSIGNMENT_FEEDBACK_PROMPT_OPENAI

    # Mirrors FeedbackGiver.generate_feedback
    return [CODE_ASSIGNMENT_FEEDBACK_PROMPT_OPENAI.format(
        course_name=FIXTURE_COURSE, assignment=FIXTURE_INSTRUCTIONS, solution=FIXTURE_SOLUTION,
        submission=FIXTURE_SUBMISSION, feedback_types="\n\t".join(FIXTURE_FEEDBACK_TYPES))]


def _render_flowgorithm_grade() -> list[str]:
    from cqc_cpcc.flowgorithm_grading import build_flowgorithm_prompt

    return [build_flowgorithm_prompt(FIXTURE_INSTRUCTIONS, FIXTURE_RUBRIC_TABLE, FIXTURE_SUBMISSION,
                                     FIXTURE_FILE_NAME, FIXTURE_TOTAL_POINTS)]


def _render_structured_fallback() -> list[str]:
    from cqc_cpcc.requirement_coverage import RequirementChecklist
    from cqc_cpcc.utilities.AI.openai_client import _build_fallback_prompt

    return [_build_fallback_prompt("Extract the requirements.", RequirementChecklist)]


RENDERERS: dict[str, Callable[[], list[str]]] = {
    "rubric-grading": _render_rubric_grading,
    "requirements-section": _render_requirements_section,
    "requirement-extraction": _render_requirement_extraction,
    "exam-grading": _render_exam_grading,
    "preprocessing-digest": _render_preprocessing_digest,
    "project-feedback": _render_project_feedback,
    "flowgorithm-grade": _render_flowgorithm_grade,
    "structured-fallback": _render_structured_fallback,
}


# --- Fingerprints ---------------------------------------------------------------------

def requests_for(prompt_id: str, entry: PromptEntry) -> list[dict]:
    """The request bodies (without model params) the renders produce."""
    from cqc_cpcc.utilities.AI.openrouter_client import (
        build_messages,
        build_response_format,
    )

    renderer = RENDERERS.get(prompt_id)
    if renderer is None:
        raise KeyError(f"No renderer for prompt {prompt_id!r}; add one to RENDERERS")
    response_format = build_response_format(import_ref(entry.schema_model)) if entry.schema_model else None
    return [{"messages": build_messages(text), "response_format": response_format}
            for text in renderer()]


def fingerprint(prompt_id: str, entry: PromptEntry) -> str:
    payload = json.dumps(requests_for(prompt_id, entry), sort_keys=True, ensure_ascii=False)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_all(registry: Optional[PromptRegistry] = None) -> dict[str, str]:
    registry = registry or load()
    return {pid: fingerprint(pid, entry) for pid, entry in registry.prompts.items()}


def mismatches(registry: Optional[PromptRegistry] = None) -> dict[str, tuple[str, str]]:
    """``{prompt_id: (recorded, computed)}`` for every stale fingerprint."""
    registry = registry or load()
    computed = compute_all(registry)
    return {pid: (entry.fingerprint, computed[pid]) for pid, entry in registry.prompts.items()
            if entry.fingerprint != computed[pid]}


def write_fingerprints(path: Path = REGISTRY_PATH, bump: bool = False,
                       base: Optional[PromptRegistry] = None) -> dict[str, str]:
    """Rewrite stale fingerprints in ``path``; return ``{prompt_id: new_fingerprint}``.

    With ``base`` (the registry at the base ref), a changed prompt whose version is not
    above the base version is refused unless ``bump`` (then its version is incremented).
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    registry = PromptRegistry.model_validate(data)
    changed = {pid: new for pid, (_, new) in mismatches(registry).items()}
    for pid, new in changed.items():
        entry = data["prompts"][pid]
        base_version = base.prompts[pid].version if base and pid in base.prompts else 0
        if entry["version"] <= base_version:
            if not bump:
                raise ValueError(f"{pid} changed: bump its version above {base_version} "
                                 "(or pass --bump)")
            entry["version"] = base_version + 1
        entry["fingerprint"] = new
    if changed:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return changed


def load_at_ref(ref: str) -> Optional[PromptRegistry]:
    """The registry as committed at git ``ref`` (None when the file did not exist there)."""
    shown = subprocess.run(["git", "show", f"{ref}:{REGISTRY_RELPATH}"], cwd=REPO_ROOT,
                           capture_output=True, text=True)
    if shown.returncode != 0:
        if "does not exist" in shown.stderr or "exists on disk, but not in" in shown.stderr:
            return None
        raise RuntimeError(f"git show {ref}:{REGISTRY_RELPATH} failed: {shown.stderr.strip()}")
    return PromptRegistry.model_validate_json(shown.stdout)


def version_problems(head: PromptRegistry, base: Optional[PromptRegistry]) -> list[str]:
    """Prompts whose fingerprint changed since ``base`` without a version bump."""
    if base is None:
        return []
    problems = []
    for pid, entry in head.prompts.items():
        before = base.prompts.get(pid)
        if before is None:
            continue
        if entry.fingerprint != before.fingerprint and entry.version <= before.version:
            problems.append(f"{pid}: fingerprint changed but version stayed {entry.version}")
        if entry.version < before.version:
            problems.append(f"{pid}: version went down ({before.version} -> {entry.version})")
    return problems


# --- Call-site discovery (keeps unregistered prompts out) ------------------------------

#: Calls that send (or template) a prompt for a model. Sites that only forward a prompt
#: built elsewhere, or render one without sending it, are listed in NON_PROMPT_SITES.
LLM_CALL_ATTRS = ("structured", "completions.create")
LLM_CALL_NAMES = ("PromptTemplate",)
NON_PROMPT_SITES = frozenset({
    "cqc_cpcc.utilities.AI.openrouter_client:_get_openrouter_completion_impl",  # gateway transport
})


def _dotted(node) -> str:
    import ast

    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


@lru_cache(maxsize=1)
def discover_call_sites(src_root: Path = REPO_ROOT / "src") -> frozenset:
    """Every ``module:function`` under ``src/`` that sends a prompt to a model."""
    import ast

    sites = set()
    for path in sorted(src_root.rglob("*.py")):
        module = ".".join(path.relative_to(src_root).with_suffix("").parts)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

        def visit(node, scope):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    visit(child, scope + [child.name])
                    continue
                if isinstance(child, ast.Call):
                    name = _dotted(child.func)
                    hit = (any(name == n or name.endswith("." + n) for n in LLM_CALL_NAMES)
                           or any(name.endswith("." + a) for a in LLM_CALL_ATTRS))
                    if hit:
                        sites.add(f"{module}:{'.'.join(scope) or '<module>'}")
                visit(child, scope)

        visit(tree, [])
    return frozenset(sites)
