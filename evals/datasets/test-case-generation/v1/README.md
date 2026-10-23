# test-case-generation dataset (v1)

Synthetic cases for the `test-case-generation` prompt suite: assignment instructions plus a
reference solution in, stdin test cases out. **No case derives from a real submission, a real
assignment export or any student work.** The six assignments (Payroll and OrderTotal in Java,
grades and guess in Python, shipping and temperature in C++) are written in
`src/cqc_cpcc/model_eval/suite_datasets.py` (`TESTCASE_SPECS`), each in a numbered and a prose
wording, 12 cases in all. Every program carries the allowlisted synthetic author name from
`scripts/pii_guard.py`.

Regenerate with:

```bash
poetry run python -m cqc_cpcc.model_eval build-suite-datasets
```

`cases.jsonl` is generated; a unit test fails when it differs from the builder. Do not edit it
by hand.

Labels (`labels.conditions`) come from construction, so they are drafts until a human reviews
them (`labels_reviewed_by` is null). Each names a selection or loop condition the instructions
state, keyword groups that recognise it in the model's list, and the input values that sit on
its boundary (for example `40` and `41` for "over 40 hours", `0` and `81` for a range check).

Graders (all deterministic; no program is run): `condition_recall`, `boundary_hit`,
`map_complete`, `case_limit`. See `src/cqc_cpcc/model_eval/suites/stdin_cases.py`.
