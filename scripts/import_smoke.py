#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Dependency smoke test: import every first-party module on the installed lock.

Used by the prompt-eval workflow when a change touches only the lockfile, the project
file or docs (``prompts-affected`` reports ``dependency_only``). A removed or broken
package shows up here as an ImportError, at no model cost. Exits 1 on any failure.

Usage: python scripts/import_smoke.py [package ...]   (default: cqc_cpcc)
"""

from __future__ import annotations

import importlib
import pkgutil
import sys
import traceback


def iter_modules(package_name: str):
    package = importlib.import_module(package_name)
    yield package_name
    for info in pkgutil.walk_packages(package.__path__, prefix=f"{package_name}."):
        yield info.name


def smoke(package_names) -> list[tuple[str, str]]:
    failures = []
    for package_name in package_names:
        try:
            names = list(iter_modules(package_name))
        except Exception:  # the package itself does not import
            failures.append((package_name, traceback.format_exc(limit=1)))
            continue
        for name in names:
            if name.rsplit(".", 1)[-1] == "__main__":
                continue  # entry points run their CLI on import
            try:
                importlib.import_module(name)
            except Exception:
                failures.append((name, traceback.format_exc(limit=1)))
    return failures


def main(argv=None) -> int:
    packages = (argv if argv is not None else sys.argv[1:]) or ["cqc_cpcc"]
    failures = smoke(packages)
    for name, tb in failures:
        print(f"FAIL {name}\n{tb}", file=sys.stderr)
    print(f"import smoke: {len(failures)} failure(s) across {', '.join(packages)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
