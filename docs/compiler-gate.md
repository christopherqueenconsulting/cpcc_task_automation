# The Compile Gate

**A real compiler decides whether student code compiles — not the LLM.**

## Why this exists

The model's compile judgment is unreliable in a way that costs students marks:
gpt-5-mini flagged valid C++ as non-compiling, and because "Does Not Compile" is a
major error, that single false positive floored the whole grade.

A compiler is not a matter of opinion. `utilities/compiler_gate.py` runs the real
local toolchain over the submission and reconciles the model's `detected_errors`
against hard evidence.

## What it does

`apply_compile_gate` runs in `grade_with_rubric` **immediately before**
`apply_backend_scoring`. The order is load-bearing: the gate corrects the error list,
and the score and band are computed from that list.

| Compiler says | Model said | Gate action |
|---|---|---|
| Compiles | "Does Not Compile" | **Removes** the error (false positive) |
| Compiles | nothing | no change |
| Does not compile | "Does Not Compile" | **Confirms** (the error stays as the model wrote it) |
| Does not compile | nothing | **Adds** the rubric's compile error, with instructor-only diagnostics |
| Cannot verify | anything | **Nothing** — the model's judgment stands |

When the error list changes, the cached `error_counts_by_*` are reset to `None` so
scoring recomputes from the corrected list rather than the model's counts.

Any correction is logged at WARNING. Overriding the model on something that moves a
grade is not something to do quietly, and `gate_report` lets the caller surface the
verdict to the instructor.

## "Cannot verify" is not "does not compile"

This is the distinction the whole design turns on. If `javac` is missing from a CI
box — or a compile times out — the gate returns `supported=False`, **not**
`compiles=False`. Conflating the two would fail every Java submission on a machine
without a JDK.

Non-definitive outcomes, all of which leave the model's judgment alone:

- no `g++`/`clang++` on PATH; no `javac` on PATH
- the compile timed out (`COMPILE_TIMEOUT_SECONDS`)
- an unsupported language (SAS has no local compiler)
- an empty submission, or no compilable source files at all
- a C/C++ include that could read a file outside the submission (see below)

In a mixed-language submission, one unverifiable language makes the whole submission
unverifiable. There is no half-verdict.

## It never runs student code

| Language | Tool | Why it cannot execute |
|---|---|---|
| Python | built-in `compile(code, name, "exec")` | Parses only; produces a code object and never evaluates it |
| C++ | `g++ -fsyntax-only -std=c++17` | Front end only — no assembly, no link, no executable |
| Java | `javac -d <tmpdir>` | Produces `.class` files; nothing invokes `java` |

Every invocation is time-bounded, passes list arguments (never `shell=True`), and
works inside a `TemporaryDirectory` that is removed afterwards.

`g++ -fsyntax-only` still resolves `#include`, and its diagnostics quote the lines of
any file it opens. A submission that includes a host file (for example the app's
`.env`) would read it back in the error text. So before compiling, the gate refuses a
verdict (`supported=False`) when any C/C++ file has:

- an `#include`, `#import` or `#embed` of an absolute path, or of a path containing `..`;
- a quoted include (`"name.h"`) that is not one of the submitted files, by basename;
- `#include_next`, or a macro include (`#include NAME`);
- `__has_include` or `__has_embed`, which can test whether a host file exists.

Standard headers (`<iostream>`) and the submission's own headers still compile. The
scan also catches the `%:` digraph, the `??=` trigraph, comments before the keyword
and backslash line splices. It matches anywhere in the text, so a directive-like
string or comment can make a submission unverifiable; that is the safe direction.

One residual gap, not currently exploited by the tool but worth knowing: `javac` runs
annotation processors by default (`-proc:none` would close that).

## Multi-file submissions

Files are grouped by language and compiled **together** in one temp directory, so a
multi-file C++ program with cross-file `#include`s, or Java classes referring to each
other, is not spuriously reported as non-compiling. Java sources are rewritten to the
filename `javac` demands (named after the public top-level type), because a submission
rarely arrives that way. A C++ submission that is headers only is syntax-checked as
headers rather than skipped.

Compiler diagnostics have the throwaway temp path replaced with the student's real
filename before they reach a report, and are truncated at 1500 characters — g++ can
emit thousands of lines for one mistake. They are kept in the instructor-only
`DetectedError.compiler_diagnostics` field, which is outside the model's schema. The
student-facing `notes` get only a generic "Verified by g++: the submission does not
compile." line, so student feedback never shows raw diagnostics.

## Configuration

| Setting | Default | Meaning |
|---|---|---|
| `COMPILE_TIMEOUT_SECONDS` | see module | Per-invocation ceiling; exceeding it yields "could not verify" |
| `CPP_STD` | `c++17` | Passed to `g++ -std=` |

## Testing

```bash
poetry run pytest tests/unit/test_compiler_gate.py tests/unit/test_backend_scoring.py -q
```

The toolchain-missing and timeout paths are tested with the compiler stubbed out, so
the suite behaves the same on a machine with no JDK as on one with a full toolchain.
