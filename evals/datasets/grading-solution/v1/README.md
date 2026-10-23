# grading-solution dataset (v1)

Synthetic cases for the `grading-solution` prompt suite: rubric grading with the
assignment's reference solution in the prompt. **No case derives from a real submission.**
Every program is generated from the two synthetic assignments in
`src/cqc_cpcc/model_eval/dataset_builder.py` (CSC 151 Exam 1 in Java, CSC 134 Project in
C++, and the Java assignment relabelled for CSC 251), and every author name is one of the
allowlisted synthetic names in `scripts/pii_guard.py`.

Regenerate with:

```bash
poetry run python -m cqc_cpcc.model_eval build-solution-dataset
```

Each case directory holds the submission, `case.json` and `solution/<file>`, the clean
program by Ada Example that `case.json` names in `reference_solution_file`.

Case kinds (`tags`):

- `follows_solution_format` (one per course): the clean program itself, another author.
- `equivalent`: a rewrite with the same observable behaviour (a helper method, reordered
  independent declarations, `String.format` for `printf`, a `do`/`while` for a `while`, a
  ternary for an `if`, other variable names). Labels: no error.
- `bug`: a twin of the solution with one labelled error from the course's error
  definitions (`expected.error_ids`), including one program that does not compile per
  course. `slight_format` marks the twins whose only error is output formatting.

`twin_of` names the course's `follows_solution_format` case.
