# Rubric-Based Grading System

This document explains how to use the flexible rubric system for automated grading in the CPCC Task Automation platform.

## Overview

The rubric system provides:
- **Config-driven rubrics** stored as JSON strings in code
- **Course-scoped rubrics** with filtering by course ID (e.g., CSC151, CSC152)
- **Streamlit UI overrides** that take precedence over config values
- **OpenAI-powered grading** returning structured rubric breakdowns
- **Error detection** integration with existing error definitions
- **Single-shot structured outputs** (no RAG or vector DB required)

## Table of Contents

1. [Rubric Structure](#rubric-structure)
2. [Scoring Modes](#scoring-modes)
3. [Course-Scoped Rubrics](#course-scoped-rubrics)
4. [Adding a New Rubric](#adding-a-new-rubric)
5. [Using Rubrics in Code](#using-rubrics-in-code)
6. [Streamlit UI Workflow](#streamlit-ui-workflow)
7. [Streamlit UI Overrides](#streamlit-ui-overrides)
8. [Understanding Results](#understanding-results)
9. [Error Definitions](#error-definitions)
10. [Examples](#examples)
11. [Built-in Rubrics](#built-in-rubrics)

---

## Rubric Structure

A rubric consists of:
- **Metadata**: ID, version, title, description
- **Course Association**: List of course IDs this rubric applies to
- **Criteria**: Individual grading aspects with max points
- **Performance Levels** (optional): Score ranges with descriptive anchors
- **Overall Bands** (optional): Total score ranges for holistic performance labels

### JSON Schema

```json
{
    "rubric_id": "unique_identifier",
    "rubric_version": "1.0",
    "title": "Human-Readable Title",
    "description": "Optional description of rubric purpose",
    "course_ids": ["CSC151", "CSC152"],
    "criteria": [
        {
            "criterion_id": "stable_id",
            "name": "Criterion Name",
            "description": "What this criterion assesses (optional)",
            "max_points": 25,
            "enabled": true,
            "levels": [
                {
                    "label": "Exemplary",
                    "score_min": 23,
                    "score_max": 25,
                    "description": "Anchor text for top performance"
                },
                {
                    "label": "Proficient",
                    "score_min": 18,
                    "score_max": 22,
                    "description": "Anchor text for solid performance"
                }
            ]
        }
    ],
    "overall_bands": [
        {
            "label": "Exemplary",
            "score_min": 90,
            "score_max": 100
        }
    ]
}
```

### Validation Rules

1. **Total Points**: `total_points_possible = sum(criteria.max_points)` for enabled criteria
2. **Level Ranges**: `0 <= score_min <= score_max <= criterion.max_points`
3. **Overall Bands**: `band.score_max <= total_points_possible`
4. **Non-overlapping**: Levels should ideally not overlap (validated with warnings)

---

## Scoring Modes

The rubric system supports three scoring modes for criteria, allowing flexible grading strategies:

### Manual Scoring (`scoring_mode: "manual"`)

**Use when**: The LLM or human directly assigns points.

In manual mode, the grader (LLM or human) assigns points directly within the criterion's range (0 to max_points). The backend does not modify these points.

```json
{
    "criterion_id": "creativity",
    "name": "Creativity & Originality",
    "max_points": 20,
    "enabled": true,
    "scoring_mode": "manual"
}
```

**How it works**:
- LLM evaluates the submission and assigns points (e.g., 17/20)
- Backend uses the LLM-assigned points as-is
- Best for subjective criteria where precise scoring rules are difficult to define

---

### Level-Band Scoring (`scoring_mode: "level_band"`)

**Use when**: You want the LLM to select a performance level, but the backend computes exact points deterministically.

In level-band mode, the LLM selects which performance level the submission achieves (e.g., "Proficient", "Exemplary"), and the backend computes the exact points using a strategy (min, mid, or max of the level's range).

```json
{
    "criterion_id": "analysis",
    "name": "Intelligence Analysis",
    "max_points": 30,
    "enabled": true,
    "scoring_mode": "level_band",
    "points_strategy": "min",
    "levels": [
        {
            "label": "Exemplary",
            "score_min": 27,
            "score_max": 30,
            "description": "Insightful and nuanced analysis"
        },
        {
            "label": "Proficient",
            "score_min": 23,
            "score_max": 26,
            "description": "Good analysis with relevant examples"
        },
        {
            "label": "Developing",
            "score_min": 18,
            "score_max": 22,
            "description": "Basic analysis with limited depth"
        },
        {
            "label": "Beginning",
            "score_min": 0,
            "score_max": 17,
            "description": "Superficial or missing analysis"
        }
    ]
}
```

**How it works**:
1. LLM evaluates the submission and selects a level label (e.g., "Proficient")
2. Backend finds the matching level (Proficient = 23-26 points)
3. Backend computes exact points using the `points_strategy`:
   - `"min"`: Award `score_min` (23 points) - **default, most conservative**
   - `"mid"`: Award midpoint `(score_min + score_max) // 2` (24 points)
   - `"max"`: Award `score_max` (26 points) - most generous

**Benefits**:
- **Prevents "math drift"**: LLM doesn't compute totals or do arithmetic
- **Consistent scoring**: Same level always maps to same points (given strategy)
- **Traceable**: Easy to see which level was selected and how points were computed
- **Flexible**: Can adjust strategy (min/mid/max) without changing the rubric structure

**When to use**:
- Reflection papers, essays, or written assignments with descriptive performance levels
- Any assignment where you want human-readable performance labels
- When you trust the LLM's judgment on *quality* but not on *arithmetic*

---

### Error-Count Scoring (`scoring_mode: "error_count"`)

**Use when**: Points are computed by deducting for detected errors (e.g., programming exams).

In error-count mode, the LLM detects errors (major and minor), and the backend computes points by applying deductions based on error counts.

```json
{
    "criterion_id": "correctness",
    "name": "Program Correctness",
    "max_points": 50,
    "enabled": true,
    "scoring_mode": "error_count",
    "error_rules": {
        "major_weight": 10,
        "minor_weight": 2,
        "floor_score": 0,
        "max_deduction": 50,
        "error_conversion": {
            "minor_to_major_ratio": 4
        }
    }
}
```

**How it works**:
1. LLM detects errors and categorizes them (major vs. minor)
2. Backend applies optional error conversion (e.g., 4 minor = 1 major)
3. Backend computes deductions: `deduction = (major_count * major_weight) + (minor_count * minor_weight)`
4. Backend computes points: `points = max_points - deduction`
5. Backend applies optional caps: `floor_score` (minimum) and `max_deduction` (cap on losses)

**Error Conversion Rules**:

The `error_conversion` field allows you to normalize error counts before scoring. This is useful for rubrics like CSC151 where "4 minor errors = 1 major error".

```json
"error_conversion": {
    "minor_to_major_ratio": 4
}
```

**Example**:
- Original counts: 1 major, 5 minor
- After conversion: 2 major, 1 minor (5 minor ÷ 4 = 1 major + 1 minor remainder)
- Deduction: `2*10 + 1*2 = 22 points`
- Final score: `50 - 22 = 28 points`

**When to use**:
- Programming exams with clear error categories
- Any assignment where grading is based on counting mistakes
- When you want consistent, objective scoring based on error counts

---

### Choosing a Scoring Mode

| Scoring Mode    | LLM Role                          | Backend Role                 | Best For                          |
|-----------------|-----------------------------------|------------------------------|-----------------------------------|
| **manual**      | Assigns points directly           | Uses LLM points as-is        | Subjective criteria               |
| **level_band**  | Selects performance level         | Computes points from level   | Essays, reflections, analysis     |
| **error_count** | Detects and counts errors         | Computes points via deductions| Programming exams, technical work |

**General Principle**: Use `level_band` or `error_count` whenever you want the backend to handle arithmetic. This prevents "math drift" where the LLM computes incorrect totals.

---

## Course-Scoped Rubrics

Rubrics can be associated with one or more courses, allowing course-specific filtering in the Streamlit UI.

### Course IDs

Course IDs are short identifiers like:
- `"CSC151"` - Computer Science I
- `"CSC152"` - Computer Science II  
- `"CSC251"` - Advanced Computer Science

### Defining Course Association

In the rubric JSON, include the `course_ids` field:

```json
{
    "rubric_id": "java_basics_100pt",
    "rubric_version": "1.0",
    "title": "Java Basics Rubric",
    "course_ids": ["CSC151", "CSC152"],
    "criteria": [...]
}
```

### Multiple Courses

A rubric can apply to multiple courses:

```json
"course_ids": ["CSC151", "CSC152", "CSC251"]
```

### Unassigned Rubrics

If `course_ids` is omitted or empty, the rubric defaults to `["UNASSIGNED"]`.

### Filtering by Course

Use helper functions to work with course-scoped rubrics:

```python
from cqc_cpcc.rubric_config import (
    get_distinct_course_ids,
    get_rubrics_for_course
)

# Get all courses
course_ids = get_distinct_course_ids()
print(course_ids)  # ['CSC151', 'CSC152', 'CSC251']

# Get rubrics for a specific course
csc151_rubrics = get_rubrics_for_course("CSC151")
for rubric_id, rubric in csc151_rubrics.items():
    print(f"{rubric.title}: {rubric.total_points_possible} pts")
```

### Streamlit Course Selection

In the Streamlit UI (Grade Assignment → Exams (Rubric) tab):

1. **Course Dropdown**: Lists all distinct course IDs from rubrics
2. **Rubric Dropdown**: Filtered to show only rubrics for selected course
3. **Session Persistence**: Course selection persists across submissions
4. **Automatic Reset**: Rubric selection resets when course changes

---

## Adding a New Rubric

### Step 1: Edit Configuration File

Open `src/cqc_cpcc/rubric_config.py` and locate `RUBRICS_JSON`.

### Step 2: Add Your Rubric JSON

Add a new entry to the JSON object:

```python
RUBRICS_JSON = """{
    "existing_rubric": { ... },
    "my_new_rubric": {
        "rubric_id": "my_new_rubric",
        "rubric_version": "1.0",
        "title": "My Custom Rubric",
        "description": "Grading rubric for XYZ assignment",
        "course_ids": ["CSC151"],
        "criteria": [
            {
                "criterion_id": "understanding",
                "name": "Understanding & Correctness",
                "max_points": 30,
                "enabled": true,
                "levels": [
                    {
                        "label": "Exemplary",
                        "score_min": 27,
                        "score_max": 30,
                        "description": "Complete understanding with excellent implementation"
                    },
                    {
                        "label": "Proficient",
                        "score_min": 21,
                        "score_max": 26,
                        "description": "Good understanding with solid implementation"
                    },
                    {
                        "label": "Developing",
                        "score_min": 15,
                        "score_max": 20,
                        "description": "Partial understanding with some gaps"
                    },
                    {
                        "label": "Beginning",
                        "score_min": 0,
                        "score_max": 14,
                        "description": "Limited understanding"
                    }
                ]
            },
            {
                "criterion_id": "completeness",
                "name": "Completeness",
                "max_points": 40,
                "enabled": true,
                "levels": [...]
            },
            {
                "criterion_id": "quality",
                "name": "Code Quality",
                "max_points": 30,
                "enabled": true,
                "levels": [...]
            }
        ],
        "overall_bands": [
            {
                "label": "Exemplary",
                "score_min": 90,
                "score_max": 100
            },
            {
                "label": "Proficient",
                "score_min": 75,
                "score_max": 89
            },
            {
                "label": "Developing",
                "score_min": 60,
                "score_max": 74
            },
            {
                "label": "Beginning",
                "score_min": 0,
                "score_max": 59
            }
        ]
    }
}"""
```

### Step 3: Verify

Run tests to validate your rubric:

```bash
poetry run pytest tests/unit/test_rubric_config.py -v
```

---

## Using Rubrics in Code

### Load a Rubric

```python
from cqc_cpcc.rubric_config import get_rubric_by_id, list_available_rubrics

# List available rubrics
rubric_ids = list_available_rubrics()
print(rubric_ids)  # ['default_100pt_rubric', 'my_new_rubric']

# Load a specific rubric
rubric = get_rubric_by_id("my_new_rubric")
print(rubric.title)
print(rubric.total_points_possible)
```

### Grade with a Rubric

```python
from cqc_cpcc.rubric_grading import grade_with_rubric
from cqc_cpcc.rubric_config import get_rubric_by_id, load_error_definitions_from_config

# Load rubric and error definitions
rubric = get_rubric_by_id("default_100pt_rubric")
error_definitions = load_error_definitions_from_config()

# Grade a submission
result = await grade_with_rubric(
    rubric=rubric,
    assignment_instructions="Write a Hello World program...",
    student_submission="public class Hello { ... }",
    reference_solution="public class Hello { ... }",  # Optional
    error_definitions=error_definitions,  # Optional
)

# Access results
print(f"Score: {result.total_points_earned}/{result.total_points_possible}")
print(f"Overall: {result.overall_band_label}")
print(result.overall_feedback)

for criterion_result in result.criteria_results:
    print(f"{criterion_result.criterion_name}: {criterion_result.points_earned}/{criterion_result.points_possible}")
    print(f"  Level: {criterion_result.selected_level_label}")
    print(f"  Feedback: {criterion_result.feedback}")
```

### Reusable Grader

```python
from cqc_cpcc.rubric_grading import RubricGrader

# Create grader with configuration
grader = RubricGrader(
    rubric=rubric,
    assignment_instructions="Write Hello World...",
    reference_solution="...",  # Optional
    error_definitions=error_definitions,  # Optional
)

# Grade multiple submissions
for student_submission in submissions:
    result = await grader.grade(student_submission)
    print(f"Score: {result.total_points_earned}")
```

---

## Streamlit UI Workflow

The Streamlit UI provides a complete rubric-based grading workflow in the **Grade Assignment → Exams (Rubric)** tab.

### Step-by-Step Process

1. **Select Course**
   - Choose from dropdown of available courses (CSC 151, CSC 152, etc.)
   - Selection persists in session state for grading multiple students

2. **Select Rubric**
   - Dropdown automatically filters to rubrics for selected course
   - Shows rubric title, version, and total points
   - Selection persists until course changes

3. **Edit Rubric Criteria** (Optional)
   - View all criteria in editable table
   - Toggle enabled/disabled per criterion
   - Edit criterion names and max points
   - Changes are applied as overrides (doesn't modify config)

4. **Upload Assignment Instructions**
   - Supported formats: .txt, .docx, .pdf
   - Optional markdown conversion
   - Can preview instructions before grading

5. **Upload Solution** (Optional)
   - Helps AI compare student work to expected solution
   - Supports multiple files
   - Auto-detects language for proper formatting

6. **Enable Error Definitions** (Optional)
   - Checkbox to include pre-defined error detection
   - Loads errors from configuration
   - Detected errors appear in results

7. **Upload Student Submissions**
   - Single or multiple files supported
   - Processes each submission independently
   - Shows progress for each student

8. **View Results**
   - Total score and percentage
   - Performance band label
   - Per-criterion breakdown with feedback
   - Evidence snippets from code
   - Detected errors (if enabled)

### UI Features

- **Real-time validation**: Overrides validated before grading
- **Progress indicators**: Status updates for each grading operation
- **Expandable sections**: Criterion details collapsed by default
- **Error handling**: Clear error messages for validation failures
- **Course persistence**: Selected course remembered across submissions

---

## Streamlit UI Overrides

The Streamlit UI allows instructors to override rubric values before grading.

### Override Precedence

**UI overrides ALWAYS take precedence over config values.**

1. Load base rubric from config
2. Collect overrides from UI (table edits)
3. Merge: `effective_rubric = merge_rubric_overrides(base_rubric, overrides)`
4. Validate merged rubric
5. Grade using effective rubric

### Supported Overrides

```python
from cqc_cpcc.rubric_overrides import RubricOverrides, CriterionOverride, PerformanceLevelOverride

overrides = RubricOverrides(
    # Override rubric metadata
    title_override="Custom Title for This Assignment",
    
    # Override criteria
    criterion_overrides={
        "understanding": CriterionOverride(
            max_points=30,  # Change from 25 to 30
            enabled=True,
        ),
        "style": CriterionOverride(
            enabled=False,  # Disable this criterion
        ),
        "quality": CriterionOverride(
            name="Code Quality & Clarity",  # Change name
            levels_overrides={
                "Exemplary": PerformanceLevelOverride(
                    score_min=28,
                    score_max=30,
                )
            }
        ),
    },
    
    # Override overall bands
    overall_bands_overrides={
        "Exemplary": OverallBandOverride(score_min=85, score_max=110)
    }
)

# Merge with base rubric
from cqc_cpcc.rubric_overrides import merge_rubric_overrides
effective_rubric = merge_rubric_overrides(base_rubric, overrides)
```

### Important Notes

- **Disabled criteria** are excluded from `total_points_possible`
- **Level ranges** must fit within criterion `max_points`
- **Overall bands** must fit within `total_points_possible`
- Validation errors provide clear feedback

---

## Understanding Results

### RubricAssessmentResult

The grading function returns a `RubricAssessmentResult` object:

```python
result = RubricAssessmentResult(
    rubric_id="default_100pt_rubric",
    rubric_version="1.0",
    total_points_possible=100,
    total_points_earned=85,
    criteria_results=[...],  # Per-criterion results
    overall_band_label="Proficient",
    overall_feedback="Strong work with minor improvements needed...",
    detected_errors=[...]  # Optional
)
```

### Criterion Results

Each criterion has a detailed result:

```python
criterion_result = CriterionResult(
    criterion_id="understanding",
    criterion_name="Understanding & Correctness",
    points_possible=25,
    points_earned=22,
    selected_level_label="Proficient",
    feedback="Good understanding with minor gaps in edge case handling",
    evidence=[
        "Correctly implements main algorithm",
        "Handles most input cases properly"
    ]
)
```

### Detected Errors

If error definitions are provided:

```python
detected_error = DetectedError(
    code="CSC_151_EXAM_1_SYNTAX_ERROR",
    name="Syntax Error",
    severity="minor",
    description="Missing semicolon on line 42",
    occurrences=2,
    notes="Check all statement endings"
)
```

---

## Error Definitions

Error definitions are stored alongside rubrics in `rubric_config.py`.

### Current Error Definitions

Located in `ERROR_DEFINITIONS_JSON` as a JSON array:

```json
[
    {
        "code": "CSC_151_EXAM_1_INSUFFICIENT_DOCUMENTATION",
        "name": "Insufficient Documentation",
        "severity": "major",
        "description": "No documentation or insufficient amount of comments in the code"
    },
    {
        "code": "CSC_151_EXAM_1_SYNTAX_ERROR",
        "name": "Syntax Error",
        "severity": "minor",
        "description": "There are syntax errors in the code"
    }
]
```

### Adding New Error Definitions

Edit `ERROR_DEFINITIONS_JSON` in `rubric_config.py`:

```python
ERROR_DEFINITIONS_JSON = """[
    {
        "code": "MY_CUSTOM_ERROR",
        "name": "Custom Error",
        "severity": "major",
        "description": "Description of what this error means"
    },
    ...existing errors...
]"""
```

### Using Error Definitions

```python
from cqc_cpcc.rubric_config import load_error_definitions_from_config

errors = load_error_definitions_from_config()

# Filter by severity
major_errors = [e for e in errors if e.severity == "major"]
minor_errors = [e for e in errors if e.severity == "minor"]

# Use in grading
result = await grade_with_rubric(
    rubric=rubric,
    assignment_instructions="...",
    student_submission="...",
    error_definitions=errors  # Optional
)
```

---

## Examples

### Example 1: Basic Grading

```python
from cqc_cpcc.rubric_config import get_rubric_by_id
from cqc_cpcc.rubric_grading import grade_with_rubric

rubric = get_rubric_by_id("default_100pt_rubric")

result = await grade_with_rubric(
    rubric=rubric,
    assignment_instructions="Implement a binary search algorithm",
    student_submission="""
    def binary_search(arr, target):
        left, right = 0, len(arr) - 1
        while left <= right:
            mid = (left + right) // 2
            if arr[mid] == target:
                return mid
            elif arr[mid] < target:
                left = mid + 1
            else:
                right = mid - 1
        return -1
    """
)

print(f"Score: {result.total_points_earned}/{result.total_points_possible}")
# Output: Score: 92/100
```

### Example 2: Grading with Overrides

```python
from cqc_cpcc.rubric_overrides import RubricOverrides, CriterionOverride, merge_rubric_overrides

# Load base rubric
base_rubric = get_rubric_by_id("default_100pt_rubric")

# Define overrides (from UI or code)
overrides = RubricOverrides(
    criterion_overrides={
        "style": CriterionOverride(enabled=False),  # Skip style for this assignment
        "understanding": CriterionOverride(max_points=35),  # Increase weight
    }
)

# Merge
effective_rubric = merge_rubric_overrides(base_rubric, overrides)

# Grade with effective rubric
result = await grade_with_rubric(
    rubric=effective_rubric,
    assignment_instructions="...",
    student_submission="...",
)
```

### Example 3: Batch Grading

```python
from cqc_cpcc.rubric_grading import RubricGrader

# Setup grader once
grader = RubricGrader(
    rubric=get_rubric_by_id("default_100pt_rubric"),
    assignment_instructions="Implement quicksort",
    reference_solution=reference_code,
)

# Grade multiple submissions
results = []
for student_name, submission in submissions.items():
    result = await grader.grade(submission)
    results.append({
        "student": student_name,
        "score": result.total_points_earned,
        "feedback": result.overall_feedback
    })

# Sort by score
results.sort(key=lambda x: x["score"], reverse=True)
```

---

## Tips and Best Practices

1. **Use descriptive anchor text** in performance levels - this guides the AI grader
2. **Keep criterion names consistent** across rubrics for easier comparison
3. **Test new rubrics** with sample submissions before using in production
4. **Validate overrides** before grading to catch errors early
5. **Review AI assessments** - the system is a tool to assist, not replace, instructor judgment
6. **Adjust temperature** (default 0.2) if you want more/less deterministic grading
7. **Include reference solutions** when available - improves grading accuracy
8. **Use error definitions** for consistent detection of common issues

---

## Troubleshooting

### Validation Errors

**Error: "Level exceeds max_points"**
- Level `score_max` is greater than criterion `max_points`
- Fix: Adjust level ranges or increase criterion `max_points`

**Error: "Band exceeds total_points_possible"**
- Overall band `score_max` is greater than rubric total
- Fix: Adjust band ranges or enable more criteria

**Error: "Totals don't match"**
- Sum of criteria results doesn't match declared total
- This shouldn't happen - indicates a bug in grading logic

### Invalid JSON

If you get "Invalid JSON" errors:
1. Check for missing commas, brackets, quotes
2. Validate JSON at https://jsonlint.com
3. Ensure no trailing commas (not valid in standard JSON)

### Grading Quality Issues

If grades seem inconsistent:
1. Add more specific anchor text to performance levels
2. Include reference solution for comparison
3. Increase `max_tokens` if feedback is truncated
4. Lower `temperature` for more deterministic results (try 0.1)
5. Check that error definitions are comprehensive

---

## API Reference

See inline documentation in:
- `src/cqc_cpcc/rubric_models.py` - Data models
- `src/cqc_cpcc/rubric_config.py` - Configuration loading
- `src/cqc_cpcc/rubric_overrides.py` - Override system
- `src/cqc_cpcc/rubric_grading.py` - Grading functions

---

## Built-in Rubrics

The system includes several pre-configured rubrics for common use cases:

### 1. Default 100-Point Rubric (`default_100pt_rubric`)

**Purpose**: General-purpose rubric for programming assignments

**Scoring Mode**: Manual (all criteria)

**Total Points**: 100

**Criteria**:
- **Understanding & Correctness** (25 points): Demonstrates understanding of concepts with correct implementation
- **Completeness / Requirements Coverage** (30 points): Addresses all assignment requirements
- **Code Quality / Clarity** (25 points): Code is clear, well-structured, and maintainable
- **Style / Conventions** (20 points): Follows language conventions and coding standards

**Performance Levels**: 4 levels per criterion (Exemplary, Proficient, Developing, Beginning)

**Overall Bands**: Exemplary (90-100), Proficient (75-89), Developing (60-74), Beginning (0-59)

**When to use**: General programming assignments, projects, or labs where you want balanced assessment across multiple dimensions.

---

### 2. CSC 151 Java Exam Rubric (`csc151_java_exam_rubric`)

**Purpose**: Brightspace-aligned rubric for CSC151 Java programming exams

**Scoring Mode**: Special (uses CSC151 v2.0 program_performance criterion)

**Total Points**: 100

**Criteria**:
- **Program Performance** (100 points): Single criterion scored entirely by error counts

**Error Conversion Rule**: **4 minor errors = 1 major error** (applied before scoring)

**Performance Levels**: 9 levels based on effective error counts:
- A+ (0 errors): 96-100 points
- A (1 minor error): 91-95 points
- A- (2 minor errors): 86-90 points
- B (3 minor errors): 81-85 points
- B- (1 major error): 71-80 points
- C (2 major errors): 61-70 points
- D (3 major errors): 16-60 points
- F (4+ major errors): 1-15 points
- 0 (Not submitted): 0 points

**How it works**:
1. LLM detects major and minor errors
2. Backend converts 4 minor errors to 1 major error
3. Backend selects level based on effective error counts
4. Backend awards midpoint score for the level

**When to use**: CSC151 programming exams requiring strict Brightspace-aligned grading

---

### 3. AI Assignment Reflection Rubric (`ai_assignment_reflection_rubric`)

**Purpose**: Rubric for grading AI tool reflection assignments

**Scoring Mode**: Level-band (all criteria)

**Total Points**: 100

**Criteria**:
- **Tool Description & Usage** (25 points): Understanding of AI tool's purpose, features, and usage
- **Intelligence Analysis** (30 points): Analysis of AI intelligence, capabilities, and limitations
- **Personal Goals & Application** (25 points): Connection to personal learning goals and future applications
- **Presentation & Requirements** (20 points): Format, organization, writing quality, and completeness

**Performance Levels**: 4 levels per criterion (Exemplary, Proficient, Developing, Beginning)

**Points Strategy**: `min` (uses minimum of level range for conservative grading)

**Overall Bands**: Exemplary (90-100), Proficient (75-89), Developing (60-74), Beginning (0-59)

**Example Level Ranges** (Tool Description & Usage):
- Exemplary: 23-25 points (with `min` strategy, awards 23)
- Proficient: 19-22 points (with `min` strategy, awards 19)
- Developing: 15-18 points (with `min` strategy, awards 15)
- Beginning: 0-14 points (with `min` strategy, awards actual score_min which varies)

**How it works**:
1. LLM evaluates the reflection paper and selects a level for each criterion (e.g., "Proficient")
2. Backend computes exact points using the `min` strategy (e.g., Proficient → 19 points)
3. Backend sums criterion points to get total (e.g., 19 + 23 + 19 + 12 = 73)
4. Backend computes percentage (73/100 = 73%)
5. Backend selects overall band (73% → "Developing")

**When to use**: Reflection papers on AI tool usage, writing assignments with descriptive performance levels, or any assignment where you want level-based assessment without manual math.

**Benefits**:
- LLM focuses on quality assessment, not arithmetic
- Consistent scoring (same level always maps to same points)
- Transparent scoring (students can see exactly which level they achieved)
- No "math drift" (LLM can't compute incorrect totals)

---

### Using Built-in Rubrics

```python
from cqc_cpcc.rubric_config import list_available_rubrics, get_rubric_by_id

# List all available rubrics
rubric_ids = list_available_rubrics()
print(rubric_ids)
# Output: ['default_100pt_rubric', 'csc151_java_exam_rubric', 'ai_assignment_reflection_rubric']

# Load a specific rubric
ai_rubric = get_rubric_by_id("ai_assignment_reflection_rubric")
print(f"{ai_rubric.title}: {ai_rubric.total_points_possible} points")
# Output: AI Assignment Reflection Rubric: 100 points

# Grade with the rubric
result = await grade_with_rubric(
    rubric=ai_rubric,
    assignment_instructions="Write a reflection on using GitHub Copilot...",
    student_submission="I used GitHub Copilot to help me write code...",
)
print(f"Score: {result.total_points_earned}/{result.total_points_possible}")
print(f"Band: {result.overall_band_label}")
```

---

## Testing

Run rubric tests:

```bash
# All rubric tests
poetry run pytest tests/unit/test_rubric_*.py -v

# Specific test modules
poetry run pytest tests/unit/test_rubric_models.py -v
poetry run pytest tests/unit/test_rubric_config.py -v
poetry run pytest tests/unit/test_rubric_overrides.py -v
poetry run pytest tests/unit/test_rubric_grading.py -v

# Scoring engine tests (new)
poetry run pytest tests/unit/test_scoring_engine.py -v
poetry run pytest tests/unit/test_ai_reflection_rubric.py -v
```

---

## Future Enhancements

Potential improvements (not currently implemented):
- UI for creating new rubrics without editing code
- Rubric templates for common assignment types
- Historical grading analytics
- Multiple grader consensus (multiple AI assessments)
- Custom rubric import/export
- Criterion-level weighting adjustments
- Student self-assessment comparisons

---

For questions or issues, see project documentation or contact the maintainer.
