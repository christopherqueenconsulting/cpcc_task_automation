# General Rubric Scoring Engine v1 - Implementation Summary

## Overview

This implementation adds a flexible, deterministic scoring engine to the CPCC Task Automation platform, enabling support for multiple grading paradigms while preventing "math drift" where LLMs compute incorrect totals.

## Problem Solved

**Before**: System had special-case logic for CSC151 error-count rubrics but no support for level-based rubrics (e.g., reflection papers). LLM computed all points and totals, leading to potential arithmetic errors.

**After**: Generic scoring layer where:
- LLM identifies issues and selects performance levels
- Backend computes exact points deterministically
- System supports error-based (CSC151), level-based (AI reflections), and manual rubrics
- Math is always correct (no LLM arithmetic)

## Key Features

### 1. Three Scoring Modes

**Manual Mode** (`scoring_mode: "manual"`)
- LLM assigns points directly
- Backend uses LLM points as-is
- Use for subjective criteria

**Level-Band Mode** (`scoring_mode: "level_band"`) ⭐ **NEW**
- LLM selects performance level (e.g., "Proficient")
- Backend computes exact points from level range
- Strategies: `min` (conservative), `mid` (midpoint), `max` (generous)
- **Prevents math drift** - no LLM arithmetic

**Error-Count Mode** (`scoring_mode: "error_count"`)
- LLM detects errors (major/minor)
- Backend applies conversion rules (e.g., 4 minor = 1 major)
- Backend computes deductions and final score
- **Prevents math drift** - deterministic calculations

### 2. Deterministic Scoring Engine

**Location**: `src/cqc_cpcc/scoring/rubric_scoring_engine.py`

**Core Functions**:
- `score_level_band_criterion()` - Compute points from selected level
- `score_error_count_criterion()` - Compute points from error counts
- `aggregate_rubric_result()` - Sum totals, compute percentage, select overall band
- `compute_percentage()` - Accurate percentage calculation
- `select_overall_band()` - Deterministic band selection

**Benefits**:
- Single source of truth for all scoring math
- Fully testable in isolation
- Consistent results every time
- No floating-point errors or rounding issues

### 3. AI Assignment Reflection Rubric

**Location**: `rubric_config.py` - `ai_assignment_reflection_rubric`

**Purpose**: Grade reflection papers on AI tool usage

**Scoring**: Level-band mode (4 criteria × 4 levels each)

**Criteria**:
1. Tool Description & Usage (25 points)
2. Intelligence Analysis (30 points)
3. Personal Goals & Application (25 points)
4. Presentation & Requirements (20 points)

**Levels**: Exemplary, Proficient, Developing, Beginning

**How It Works**:
1. LLM evaluates reflection and selects a level for each criterion
2. Backend computes exact points using `min` strategy (conservative)
3. Backend sums criterion points → total
4. Backend computes percentage
5. Backend selects overall band (Exemplary/Proficient/Developing/Beginning)

**Example**:
- LLM selects: Proficient (19 pts) + Exemplary (27 pts) + Proficient (19 pts) + Developing (12 pts)
- Backend computes: 19 + 27 + 19 + 12 = **77/100 (77%, Proficient band)**

## Technical Implementation

### Models Extended (`rubric_models.py`)

**New Literal Values**:
```python
scoring_mode: Literal["manual", "level_band", "error_count"]
points_strategy: Literal["min", "mid", "max"]
```

**New Classes**:
```python
class ErrorConversionRules(BaseModel):
    minor_to_major_ratio: int = 4  # 4 minor = 1 major
```

**Updated Criterion**:
- Added `scoring_mode` support for "level_band"
- Added `points_strategy` for level-band scoring
- Added validation: level_band mode requires levels

### Prompt Updates (`rubric_grading.py`)

**Updated Instructions**:
- Clarified LLM should NOT compute totals
- Explained level_band: select label, don't assign points
- Explained error_count: detect errors, don't assign points
- Added note: "Backend will compute all point totals and percentages deterministically"

### Backend Scoring Integration

**Function**: `apply_backend_scoring(rubric, result)` (replaces `apply_error_based_scoring`)

**Process**:
1. Identify criteria needing backend scoring (level_band, error_count)
2. For level_band: call `score_level_band_criterion()` → update points
3. For error_count: apply conversion, call `score_error_count_criterion()` → update points
4. Call `aggregate_rubric_result()` → recompute totals, percentage, overall band
5. Return updated result with correct math

**Backwards Compatibility**: `apply_error_based_scoring()` wrapper maintained for legacy code.

### Counting errors

Ruled by Christopher on 2026-10-22 (rubric versions: `csc_java_exam_rubric` 3.2, `csc134_cpp_exam_rubric` 3.2,
`csc134_cpp_final_exam_rubric` 3.3):

- **Each distinct error type counts once.** An error type is its code, compared case-insensitively, across
  all files of the submission. One type reported with `occurrences: 4` counts as one error, for minors and
  majors alike. If the model labels one code both minor and major, it counts once, as major. Occurrences
  still show in the Errors table ("Times") and on the result card; they no longer move the score.
- **Only listed errors count.** `program_performance` takes its counts from `detected_errors`. Counts the
  model reports in `error_counts_by_severity` without listing the errors are ignored (they cost nothing;
  a warning is logged). Rubrics with a plain `error_count` criterion and no `program_performance` (none
  ship today) still fall back to those counts.
- **Conversion is unchanged:** every 4 distinct minor types count as 1 major (remainder stays minor), then
  the level band is chosen from the effective counts.
- `error_scoring.aggregate_error_counts` (which sums occurrences) is display-only; scoring never calls it.

Before this change one minor slip seen four times (`occurrences: 4`) became 1 major, a B- (160/200) on the
CSC151 rubric; it is now 1 minor, an A (190/200). Most scores stay the same or rise. A score can drop when
the model's own counts were lower than the errors it listed (the old code trusted the counts): those runs now
score from the list.

**Rollback:** revert the commit that made this change. That restores the earlier `rubric_version`s, so the
run keys change back and session caches re-grade on their own. A revert does not touch grades already
written to BrightSpace: assignment drafts and quiz grades (published on write) stay as written until they
are re-graded and written again. Rolling back is Christopher's call.

### One penalty per issue: requirement overlap

A requirement the model marks partial or missing becomes an error (`MISSING_REQUIREMENT_<id>` major,
`PARTIAL_REQUIREMENT_<id>` minor), and the model also lists its own errors, so one defect (for example
`Print the total with two decimal places` marked partial next to `OUTPUT_FORMATTING`) can be charged twice.
`requirement_overlap.reconcile_overlaps` pairs them after requirement coverage and before scoring. It is
deterministic and uses no model call; the grading prompt and the answer schema are not changed (asking the
model to link its own errors made it mark requirements partial far more often, PR 8).

- **Kinds.** A requirement's kind (`format`, `print`, `input`, `valid`, `func`, `select`, `loop`, `calc`,
  `data`) comes from its wording; a model error code maps to the kinds it can overlap (shared code tokens, with
  per-course overrides, then whole-word description hints for custom definitions; a definition that talks about
  comments, naming, indentation or style never pairs). Unknown on either side means no pair. A requirement that
  forbids something ("must not use loops", "without functions", "rather than") has no kind. Formatting codes
  (`OUTPUT_FORMATTING`, `DECIMAL_SCALE`, `*_FORMATTING`) pair only with a `format` requirement, which needs
  explicit format wording (decimal places, currency, separators, spacing, sample output); "Show a menu" or
  "Tell the user ..." is `print` and never pairs with them, and "Calculate the payment as currency" is left alone.
- **Only unambiguous pairs.** The requirement has exactly one kind-matching model error and that error has
  exactly one kind-matching requirement (one-to-one). Anything else gets an instructor-only "possible overlap"
  note and keeps both penalties. For the general codes (`OUTPUT_IMPACT`, `CALCULATION`, `SEQUENCE...SELECTION`) a
  kind match is not enough: the error's own description or notes must share a content word with the requirement,
  otherwise it is a note only.
- **Which one goes.** The lesser of the pair; at equal severity the requirement error is kept and the model
  error dropped (tie rule A', reversible). A `MISSING_REQUIREMENT_*` error is never dropped. That is a CORE
  requirement the model marked missing (major). A non-core "missing" requirement becomes
  `PARTIAL_REQUIREMENT_*` (minor), so it can be dropped in favour of a major model error, like any partial.
  Errors backed by the compiler or the execution check and "Does Not Compile" are never dropped either, so
  shallow-submission detection is unchanged. Every drop is a suppressed `ReconciliationNote` (stage `overlap`)
  the instructor can Reinstate; a code listed twice gets one note per entry, and a multi-occurrence entry says
  how many times it was listed.
- **Switch `CQC_REQUIREMENT_OVERLAP`** (also in `.env.example`). `notes` (default): write the notes, drop nothing,
  scores identical to a run without the stage. `drop`: apply the drops. `off`: skip the stage. Only `drop`
  enters the grading run key (as `requirement_overlap`), so existing keys and cached results do not change in
  `notes`/`off`.
- **First notes-mode session (before turning `drop` on).**
  1. Grade a real session with the default (`notes`). Scores must equal what master gives.
  2. Read every "Possible overlap" note in the drawer. Judge whether the two items really are the same defect.
  3. Check that no note flags a `MISSING_REQUIREMENT_*`, a compiler error or Does Not Compile as the one to drop.
  4. If the pairs look right, set `CQC_REQUIREMENT_OVERLAP=drop` in `.env`, restart, and re-grade (the key changes,
     so it re-grades on its own).
  5. Reinstate any drop that was wrong (Reinstate re-scores with no model call) and tell the builder which wording
     was mis-paired.
- **Rollback.** Remove the variable (or set `notes`) and restart, or revert the commit. Saved runs load either
  way (no schema change); results graded under `drop` simply re-grade because their key differed.
- **Offline gate.** `python scripts/requirement_overlap_rescore.py --raw-outputs <grading raw_outputs.jsonl>
  --extraction <requirement-extraction raw_outputs.jsonl>` re-scores saved outputs and reports rows changed, rows
  that go down (must be 0), the `incomplete` mean, and classifier coverage. Saved outputs carry codes, not the
  model's error descriptions, so general-code pairs cannot be confirmed there and show as notes.

## Testing

### New Tests (37 total)

**Scoring Engine Tests** (`test_scoring_engine.py`):
- ✅ 8 level-band scoring tests (min/mid/max strategies, invalid inputs)
- ✅ 9 error-count scoring tests (conversion rules, caps, edge cases)
- ✅ 4 aggregation tests (totals, percentage, bands)
- ✅ 9 helper function tests (percentage, band selection)

**AI Reflection Rubric Tests** (`test_ai_reflection_rubric.py`):
- ✅ 7 integration tests (rubric loads, criteria valid, backend scoring, full grading)

**All tests pass**: 37/37 ✅

### Backwards Compatibility Validated

**Existing Tests**:
- ✅ Rubric models: 29/29 tests pass
- ✅ Rubric config: 29/29 tests pass
- ✅ Total: 58 existing tests still pass

**Result**: **95 total tests pass** (37 new + 58 existing)

## Documentation

**Updated**: `docs/rubrics.md`

**New Sections**:
1. **Scoring Modes** - Detailed explanation of manual, level-band, error-count modes
2. **Comparison Table** - Guide for choosing scoring mode
3. **Error Conversion Rules** - Examples of minor→major conversion
4. **Points Strategies** - Documentation of min/mid/max strategies
5. **Built-in Rubrics** - Detailed descriptions of all 3 rubrics:
   - Default 100-Point Rubric (manual mode)
   - CSC 151 Java Exam Rubric (error-count with conversion)
   - AI Assignment Reflection Rubric (level-band, NEW)
6. **Code Examples** - Usage patterns for each rubric

## Files Changed

### New Files (3)
- `src/cqc_cpcc/scoring/__init__.py` - Package init
- `src/cqc_cpcc/scoring/rubric_scoring_engine.py` - Core scoring engine (450 lines)
- `tests/unit/test_scoring_engine.py` - Scoring engine tests (560 lines)
- `tests/unit/test_ai_reflection_rubric.py` - AI rubric tests (390 lines)

### Modified Files (4)
- `src/cqc_cpcc/rubric_models.py` - Added scoring modes, validation (+60 lines)
- `src/cqc_cpcc/rubric_config.py` - Added AI reflection rubric (+200 lines)
- `src/cqc_cpcc/rubric_grading.py` - Integrated scoring engine (+150 lines)
- `docs/rubrics.md` - Comprehensive documentation (+300 lines)

**Total**: ~1,800 lines of new code and documentation

## Usage Examples

### Using AI Reflection Rubric

```python
from cqc_cpcc.rubric_config import get_rubric_by_id
from cqc_cpcc.rubric_grading import grade_with_rubric

# Load rubric
rubric = get_rubric_by_id("ai_assignment_reflection_rubric")

# Grade a student submission
result = await grade_with_rubric(
    rubric=rubric,
    assignment_instructions="Write a reflection on using GitHub Copilot...",
    student_submission="I used GitHub Copilot extensively in this project...",
)

# Results are deterministically computed
print(f"Score: {result.total_points_earned}/{result.total_points_possible}")
# Output: Score: 77/100

print(f"Percentage: {result.total_points_earned / result.total_points_possible * 100:.1f}%")
# Output: Percentage: 77.0%

print(f"Band: {result.overall_band_label}")
# Output: Band: Proficient

# Per-criterion breakdown
for criterion in result.criteria_results:
    print(f"{criterion.criterion_name}: {criterion.selected_level_label} = {criterion.points_earned}/{criterion.points_possible}")
# Output:
# Tool Description & Usage: Proficient = 19/25
# Intelligence Analysis: Exemplary = 27/30
# Personal Goals & Application: Proficient = 19/25
# Presentation & Requirements: Developing = 12/20
```

### Creating a Custom Level-Band Rubric

```python
from cqc_cpcc.rubric_models import Rubric, Criterion, PerformanceLevel, OverallBand

rubric = Rubric(
    rubric_id="my_essay_rubric",
    rubric_version="1.0",
    title="Essay Grading Rubric",
    criteria=[
        Criterion(
            criterion_id="thesis",
            name="Thesis Statement",
            max_points=30,
            scoring_mode="level_band",
            points_strategy="mid",  # Use midpoint of range
            levels=[
                PerformanceLevel(
                    label="Excellent",
                    score_min=27,
                    score_max=30,
                    description="Clear, compelling thesis with strong argumentation"
                ),
                PerformanceLevel(
                    label="Good",
                    score_min=21,
                    score_max=26,
                    description="Clear thesis with adequate support"
                ),
                PerformanceLevel(
                    label="Needs Work",
                    score_min=0,
                    score_max=20,
                    description="Unclear or weak thesis"
                ),
            ]
        ),
        # Add more criteria...
    ],
    overall_bands=[
        OverallBand(label="A", score_min=90, score_max=100),
        OverallBand(label="B", score_min=80, score_max=89),
        OverallBand(label="C", score_min=70, score_max=79),
        OverallBand(label="D", score_min=60, score_max=69),
        OverallBand(label="F", score_min=0, score_max=59),
    ]
)
```

## Benefits

### For Instructors
- ✅ Flexible rubric system supports multiple grading styles
- ✅ No more manual math errors
- ✅ Consistent grading across students
- ✅ Transparent scoring (students see levels achieved)
- ✅ Easy to adjust point ranges without changing rubric structure

### For System
- ✅ Separates assessment (LLM) from computation (backend)
- ✅ Fully testable scoring logic
- ✅ Backwards compatible with existing rubrics
- ✅ Extensible for future scoring modes
- ✅ Single source of truth for all math

### For Students
- ✅ Clear performance level labels (not just numbers)
- ✅ Consistent scoring (same work = same score)
- ✅ Accurate totals and percentages
- ✅ Transparent grading process

## Acceptance Criteria Met

✅ **System can grade CSC151 (error_count) and AI reflection (level_band) rubrics with correct totals/percent**
- CSC151: Error conversion (4:1) + backend scoring works correctly
- AI reflection: Level selection + backend point computation works correctly

✅ **OpenAI is not relied on for points math**
- All point computation done by backend scoring engine
- LLM only selects levels or detects errors
- Backend aggregates totals, computes percentages, selects bands

✅ **UI and exported reports match engine calculations**
- Backend scoring integrated into grading flow
- All results use backend-computed values

✅ **Tests prevent regression**
- 37 new tests for scoring engine
- 58 existing tests still pass
- Total: 95 tests ✅

## Future Enhancements

Potential improvements (not in current implementation):
- **Weighted criteria**: Allow criteria to have weights (e.g., 2x weight for thesis)
- **Conditional criteria**: Enable/disable criteria based on submission type
- **Rubric versioning**: Track changes to rubrics over time
- **Analytics**: Report on score distributions, band frequencies
- **Custom strategies**: Allow user-defined point selection strategies
- **Partial credit**: Support for partial credit within error-count mode

## Conclusion

The General Rubric Scoring Engine v1 successfully implements a flexible, deterministic scoring system that:
1. Supports three distinct scoring modes (manual, level-band, error-count)
2. Prevents "math drift" by computing all points in backend
3. Maintains full backwards compatibility with existing rubrics
4. Includes comprehensive tests and documentation
5. Provides a production-ready AI reflection rubric as example

The system is ready for immediate use in grading CSC151 exams, AI reflection papers, and general programming assignments.
