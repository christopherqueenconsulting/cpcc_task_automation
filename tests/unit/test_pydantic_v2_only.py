"""Guard: first-party code uses Pydantic v2 APIs only.

Scans ``src/``, ``tests/`` and ``scripts/`` with ``ast`` (no imports, no execution).

What is flagged
---------------
Always:
  * imports of ``pydantic.v1``, ``pydantic_v1``, ``langchain_core.pydantic_v1``
  * ``from pydantic import validator, root_validator`` (the v1 names)
  * ``.parse_obj`` / ``.parse_raw`` / ``.update_forward_refs`` attribute access
  * ``.__fields__`` attribute access and the ``orm_mode`` name or keyword
  * an inner ``class Config`` inside a BaseModel subclass

Only when the receiver is statically a pydantic model (so ``patch.dict``,
``response.json()`` and ``some_dict.copy()`` are never flagged):
  * ``.dict()`` / ``.json()`` / ``.copy()`` called on
      - ``self`` / ``cls`` inside a BaseModel subclass,
      - ``Model(...)`` or ``Model.model_validate(...)`` directly,
      - a local name assigned from either of those, or annotated ``x: Model``
        (also function parameters annotated with a model class).

"Model class" = a class whose base is ``BaseModel`` (or ``pydantic.BaseModel``) or,
transitively, another model class found anywhere in the scanned files, matched by
class name. Limits: types are inferred from names only, so a model returned by a
function call, kept in a container, or reached through an attribute is not tracked
(false negatives are possible; false positives are avoided by design).

Escape hatch: put ``# pydantic-v2-guard:allow`` on the offending line.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_DIRS = ("src", "tests", "scripts")
ALLOW = "pydantic-v2-guard:allow"

V1_MODULES = ("pydantic.v1", "pydantic_v1", "langchain_core.pydantic_v1")
V1_NAMES = {"validator", "root_validator"}
V1_ALWAYS_ATTRS = {"parse_obj", "parse_raw", "update_forward_refs", "__fields__"}
V1_MODEL_METHODS = {"dict", "json", "copy"}


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    message: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.message}"


def _base_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _class_bases(cls: ast.ClassDef) -> set[str]:
    return {n for n in (_base_name(b) for b in cls.bases) if n}


def collect_model_names(trees: list[ast.AST]) -> set[str]:
    """Fixpoint over all scanned classes: names of BaseModel subclasses."""
    classes = [(c.name, _class_bases(c)) for t in trees for c in ast.walk(t) if isinstance(c, ast.ClassDef)]
    models: set[str] = set()
    changed = True
    while changed:
        changed = False
        for name, bases in classes:
            if name not in models and ("BaseModel" in bases or bases & models):
                models.add(name)
                changed = True
    return models


def _ann_model(ann: ast.expr | None, models: set[str]) -> bool:
    if ann is None:
        return False
    if isinstance(ann, ast.Constant) and isinstance(ann.value, str):
        return ann.value in models
    return _base_name(ann) in models


def _is_model_expr(node: ast.expr, models: set[str], typed: set[str]) -> bool:
    if isinstance(node, ast.Name):
        return node.id in typed
    if isinstance(node, ast.Call):
        f = node.func
        if _base_name(f) in models and not isinstance(f, ast.Attribute):
            return True  # Model(...)
        if isinstance(f, ast.Attribute) and f.attr == "model_validate":
            return _base_name(f.value) in models  # Model.model_validate(...)
    return False


class _Scanner(ast.NodeVisitor):
    def __init__(self, path: str, lines: list[str], models: set[str]):
        self.path, self.lines, self.models = path, lines, models
        self.out: list[Violation] = []
        self._class_stack: list[bool] = []  # True when the class is a model
        self._typed: list[set[str]] = [set()]

    def _add(self, node: ast.AST, msg: str) -> None:
        line = getattr(node, "lineno", 0)
        if ALLOW in (self.lines[line - 1] if 0 < line <= len(self.lines) else ""):
            return
        self.out.append(Violation(self.path, line, msg))

    # imports
    def visit_Import(self, node: ast.Import) -> None:
        for a in node.names:
            if a.name.startswith(V1_MODULES):
                self._add(node, f"import of {a.name} (pydantic v1)")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        mod = node.module or ""
        if mod.startswith(V1_MODULES):
            self._add(node, f"import from {mod} (pydantic v1)")
        if mod == "pydantic":
            for a in node.names:
                if a.name in V1_NAMES:
                    self._add(node, f"from pydantic import {a.name} (v1 name; use field_validator/model_validator)")
                if a.name == "v1":
                    self._add(node, "from pydantic import v1")

    # classes
    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        is_model = node.name in self.models
        if is_model:
            for stmt in node.body:
                if isinstance(stmt, ast.ClassDef) and stmt.name == "Config":
                    self._add(stmt, f"inner `class Config` in model {node.name} (use model_config)")
        self._class_stack.append(is_model)
        self.generic_visit(node)
        self._class_stack.pop()

    # scopes
    def _visit_func(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        typed: set[str] = set()
        args = node.args
        for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
            if _ann_model(a.annotation, self.models):
                typed.add(a.arg)
        self._typed.append(typed)
        self.generic_visit(node)
        self._typed.pop()

    visit_FunctionDef = _visit_func
    visit_AsyncFunctionDef = _visit_func

    def _cur(self) -> set[str]:
        return self._typed[-1]

    def visit_Assign(self, node: ast.Assign) -> None:
        if _is_model_expr(node.value, self.models, self._cur()):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    self._cur().add(t.id)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Name) and (
            _ann_model(node.annotation, self.models)
            or (node.value is not None and _is_model_expr(node.value, self.models, self._cur()))
        ):
            self._cur().add(node.target.id)
        self.generic_visit(node)

    # usage
    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in V1_ALWAYS_ATTRS:
            self._add(node, f".{node.attr} (pydantic v1 API)")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id == "orm_mode":
            self._add(node, "orm_mode (use from_attributes)")

    def visit_keyword(self, node: ast.keyword) -> None:
        if node.arg == "orm_mode":
            self._add(node, "orm_mode= (use from_attributes)")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr in V1_MODEL_METHODS:
            recv = f.value
            in_model = bool(self._class_stack and self._class_stack[-1])
            on_self = isinstance(recv, ast.Name) and recv.id in ("self", "cls") and in_model
            if on_self or _is_model_expr(recv, self.models, self._cur()):
                self._add(node, f".{f.attr}() on a pydantic model (v1 API; use model_dump/model_dump_json/model_copy)")
        self.generic_visit(node)


def scan_sources(sources: dict[str, str]) -> list[Violation]:
    """Scan ``{display_path: source}``; syntax errors are skipped (other tests catch them)."""
    parsed: dict[str, ast.AST] = {}
    for path, src in sources.items():
        try:
            parsed[path] = ast.parse(src)
        except SyntaxError:
            continue
    models = collect_model_names(list(parsed.values()))
    out: list[Violation] = []
    for path, tree in parsed.items():
        sc = _Scanner(path, sources[path].splitlines(), models)
        sc.visit(tree)
        out.extend(sc.out)
    return out


def scan_files(paths: list[Path], root: Path) -> list[Violation]:
    return scan_sources({str(p.relative_to(root)): p.read_text(encoding="utf-8") for p in paths})


def _first_party_files() -> list[Path]:
    files: list[Path] = []
    for d in SCAN_DIRS:
        files += [p for p in (REPO_ROOT / d).rglob("*.py") if "__pycache__" not in p.parts]
    return sorted(files)


def test_first_party_code_is_pydantic_v2_only() -> None:
    this = Path(__file__).resolve()
    files = [p for p in _first_party_files() if p.resolve() != this]
    found = scan_files(files, REPO_ROOT)
    assert not found, "pydantic v1 usage in first-party code:\n" + "\n".join(map(str, found))


# --- the scanner itself: each rule detects, and the look-alikes stay clean ---------------------

def _scan(tmp_path: Path, code: str) -> list[Violation]:
    f = tmp_path / "seeded.py"
    f.write_text(code, encoding="utf-8")
    return scan_files([f], tmp_path)


@pytest.mark.parametrize(
    "code",
    [
        "from pydantic.v1 import BaseModel\n",
        "import pydantic.v1\n",
        "import pydantic_v1\n",
        "from langchain_core.pydantic_v1 import BaseModel\n",
        "from pydantic import validator\n",
        "from pydantic import root_validator, BaseModel\n",
        "from pydantic import BaseModel\nclass M(BaseModel):\n    class Config:\n        orm_mode = True\n",
        "from pydantic import BaseModel\nclass A(BaseModel):\n    pass\nclass B(A):\n    class Config:\n        extra = 'allow'\n",
        "M.parse_obj({})\n",
        "M.parse_raw('{}')\n",
        "M.update_forward_refs()\n",
        "x = M.__fields__\n",
        "cfg = dict(orm_mode=True)\n",
        "from pydantic import BaseModel\nclass M(BaseModel):\n    def f(self):\n        return self.dict()\n",
        "from pydantic import BaseModel\nclass M(BaseModel):\n    @classmethod\n    def f(cls):\n        return cls.copy()\n",
        "from pydantic import BaseModel\nclass M(BaseModel):\n    a: int = 1\nM(a=1).json()\n",
        "from pydantic import BaseModel\nclass M(BaseModel):\n    a: int = 1\nm = M(a=1)\nm.dict()\n",
        "from pydantic import BaseModel\nclass M(BaseModel):\n    a: int = 1\ndef f(m: M):\n    return m.copy()\n",
        "from pydantic import BaseModel\nclass M(BaseModel):\n    a: int = 1\nm = M.model_validate({})\nm.json()\n",
        "from pydantic import BaseModel\nclass M(BaseModel):\n    a: int = 1\nm: M = load()\nm.dict()\n",
    ],
)
def test_guard_detects_seeded_violation(tmp_path: Path, code: str) -> None:
    assert _scan(tmp_path, code), f"guard missed:\n{code}"


@pytest.mark.parametrize(
    "code",
    [
        "from unittest import mock\nwith mock.patch.dict('os.environ', {}):\n    pass\n",
        "def f(response):\n    return response.json()\n",
        "d = {}\nc = d.copy()\n",
        "import os\nenv = os.environ.copy()\n",
        "from pydantic import BaseModel, field_validator, model_validator\n",
        "from pydantic import BaseModel\nclass M(BaseModel):\n    a: int = 1\nM(a=1).model_dump()\nM.model_validate({})\n",
        "class Plain:\n    class Config:\n        pass\n    def f(self):\n        return self.dict()\n",
        "from pydantic import BaseModel\nclass M(BaseModel):\n    a: int = 1\ndef f(m):\n    return m.dict()\n",
        "from pydantic import BaseModel\nclass M(BaseModel):\n    a: int = 1\nm = M(a=1)\nclass Other:\n    pass\no = Other()\no.copy()\n",
    ],
)
def test_guard_has_no_false_positives(tmp_path: Path, code: str) -> None:
    assert _scan(tmp_path, code) == []


def test_guard_allow_comment_suppresses(tmp_path: Path) -> None:
    code = "from pydantic import BaseModel\nclass M(BaseModel):\n    a: int = 1\nM(a=1).dict()  # pydantic-v2-guard:allow\n"
    assert _scan(tmp_path, code) == []


def test_guard_tracks_models_across_files(tmp_path: Path) -> None:
    a = tmp_path / "a.py"
    b = tmp_path / "b.py"
    a.write_text("from pydantic import BaseModel\nclass Shared(BaseModel):\n    x: int = 0\n", encoding="utf-8")
    b.write_text("from a import Shared\ndef f(s: Shared):\n    return s.json()\n", encoding="utf-8")
    assert scan_files([a, b], tmp_path)
