#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Dependency smoke test: do first-party imports still resolve on the installed lock?

Used by the prompt-eval workflow when a change touches only the lockfile, the project
file or docs (``prompts-affected`` reports ``dependency_only``). A removed or broken
package shows up here at no model cost. Exits 1 on any failure.

Three checks:

1. Import every module of each ``--import`` package (default: cqc_cpcc). ``__main__``
   modules are skipped because they run their CLI on import.
2. Resolve every absolute import statement in the source of each ``--import`` and
   ``--resolve`` package, including imports inside functions, with
   ``importlib.util.find_spec`` (no code runs). Relative imports, standard-library
   names and first-party names are skipped and counted. Imports inside a ``try`` block
   that handles ImportError are optional and skipped. ``--resolve`` is for packages
   whose modules must not be executed (cqc_streamlit_app pages call ``st.*`` at import).
3. ``--removed-since REF``: every package in ``REF``'s poetry.lock that is not in the
   working tree's poetry.lock must be absent from the environment, which proves the
   environment was synced to the head lock rather than reused from an older one.

Usage: python scripts/import_smoke.py [--import PKG ...] [--resolve PKG ...] [--removed-since REF]
"""

from __future__ import annotations

import argparse
import ast
import importlib
import importlib.metadata
import importlib.util
import pkgutil
import re
import subprocess
import sys
import traceback
from pathlib import Path

FIRST_PARTY = frozenset({"cqc_cpcc", "cqc_streamlit_app"})
IMPORT_ERRORS = frozenset({"ImportError", "ModuleNotFoundError", "Exception", "BaseException"})


def iter_modules(package_name: str):
    package = importlib.import_module(package_name)
    yield package_name
    for info in pkgutil.walk_packages(package.__path__, prefix=f"{package_name}."):
        yield info.name


def smoke(package_names) -> list[tuple[str, str]]:
    """Import every module; ``[(module, traceback)]`` for each one that fails."""
    failures = []
    for package_name in package_names:
        try:
            names = list(iter_modules(package_name))
        except BaseException:  # the package itself does not import (SystemExit included)
            failures.append((package_name, traceback.format_exc(limit=1)))
            continue
        for name in names:
            if name.rsplit(".", 1)[-1] == "__main__":
                continue  # entry points run their CLI on import
            try:
                importlib.import_module(name)
            except BaseException:  # a sys.exit at import time must not end the run
                failures.append((name, traceback.format_exc(limit=1)))
    return failures


def _package_dir(package_name: str) -> Path:
    spec = importlib.util.find_spec(package_name)
    if spec is None or not spec.submodule_search_locations:
        raise ModuleNotFoundError(package_name)
    return Path(list(spec.submodule_search_locations)[0])


def _guarded(node: ast.Try) -> bool:
    for handler in node.handlers:
        if handler.type is None:
            return True
        types = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
        if any(isinstance(t, ast.Name) and t.id in IMPORT_ERRORS for t in types):
            return True
    return False


def _imported_names(tree: ast.AST):
    """``(top-level name, line)`` of absolute imports, skipping those under an ImportError guard."""
    def walk(node, optional):
        if isinstance(node, ast.Try) and _guarded(node):
            for child in node.body:
                yield from walk(child, True)
            for child in node.handlers + node.orelse + node.finalbody:
                yield from walk(child, optional)
            return
        if isinstance(node, ast.Import) and not optional:
            for alias in node.names:
                yield alias.name.split(".")[0], node.lineno
        elif isinstance(node, ast.ImportFrom) and not optional and node.level == 0 and node.module:
            yield node.module.split(".")[0], node.lineno
        for child in ast.iter_child_nodes(node):
            yield from walk(child, optional)
    yield from walk(tree, False)


def resolve(package_names) -> tuple[list[tuple[str, str]], dict]:
    """Check every absolute import in the packages' source resolves. Returns the failures
    and the skipped names (``{"stdlib": set, "first_party": set}``)."""
    failures, skipped = [], {"stdlib": set(), "first_party": set()}
    checked: dict[str, bool] = {}
    for package_name in package_names:
        try:
            root = _package_dir(package_name)
        except BaseException:
            failures.append((package_name, traceback.format_exc(limit=1)))
            continue
        for path in sorted(root.rglob("*.py")):
            module = ".".join((package_name, *path.relative_to(root).with_suffix("").parts))
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except SyntaxError:
                failures.append((module, traceback.format_exc(limit=1)))
                continue
            for name, lineno in _imported_names(tree):
                if name in sys.stdlib_module_names or name == "__future__":
                    skipped["stdlib"].add(name)
                    continue
                if name in FIRST_PARTY:
                    skipped["first_party"].add(name)
                    continue
                if name not in checked:
                    try:
                        checked[name] = importlib.util.find_spec(name) is not None
                    except BaseException:
                        checked[name] = False
                if not checked[name]:
                    failures.append((module, f"line {lineno}: import {name} does not resolve\n"))
    return failures, skipped


def _lock_names(text: str) -> set:
    import tomllib

    return {re.sub(r"[-_.]+", "-", p["name"]).lower()
            for p in tomllib.loads(text).get("package", []) if "name" in p}


def removed_but_installed(base_lock: str, head_lock: str) -> tuple[set, list]:
    """Names in the base lock but not the head lock, and the ones still installed."""
    removed = _lock_names(base_lock) - _lock_names(head_lock)
    installed = []
    for name in sorted(removed):
        try:
            importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError:
            continue
        installed.append(name)
    return removed, installed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Dependency smoke test")
    parser.add_argument("packages", nargs="*", help="packages to import (same as --import)")
    parser.add_argument("--import", dest="imports", action="append", default=[])
    parser.add_argument("--resolve", action="append", default=[])
    parser.add_argument("--removed-since")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    imports = (args.packages + args.imports) or ([] if args.resolve else ["cqc_cpcc"])

    failures = smoke(imports)
    resolve_failures, skipped = resolve(imports + args.resolve)
    failures += resolve_failures
    for name, tb in failures:
        print(f"FAIL {name}\n{tb}", file=sys.stderr)
    print(f"import smoke: imported {', '.join(imports) or 'nothing'}; resolved imports in "
          f"{', '.join(imports + args.resolve)}; {len(failures)} failure(s)")
    print(f"skipped standard-library names: {len(skipped['stdlib'])}; "
          f"first-party names: {', '.join(sorted(skipped['first_party'])) or 'none'}")

    status = 1 if failures else 0
    if args.removed_since:
        shown = subprocess.run(["git", "show", f"{args.removed_since}:poetry.lock"],
                               capture_output=True, text=True)
        if shown.returncode != 0:
            print(f"FAIL cannot read poetry.lock at {args.removed_since}", file=sys.stderr)
            return 1
        removed, installed = removed_but_installed(shown.stdout, Path("poetry.lock").read_text(encoding="utf-8"))
        print(f"packages removed from the lock since {args.removed_since}: {len(removed)}; "
              f"still installed: {', '.join(installed) or 'none'}")
        if installed:
            status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())
