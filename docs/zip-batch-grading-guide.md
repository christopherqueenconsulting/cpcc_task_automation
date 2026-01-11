# ZIP Batch Grading Guide

## Overview

The Rubric Exam grading tab now supports batch grading of multiple students from a single ZIP file upload. This feature implements legacy-style ZIP handling with async grading and token safety.

**New in this version:** Automatic detection and handling of wrapper folders (common in BrightSpace downloads).

## ZIP Submission Format

### Folder Structure

The ZIP file should contain one folder per student, with each folder containing the student's submission files:

```
submission.zip/
  ├── Student_Name_1/
  │   ├── Main.java
  │   ├── Helper.java
  │   └── README.txt
  ├── Student_Name_2/
  │   ├── script.py
  │   └── utils.py
  └── Student_Name_3/
      └── program.cpp
```

### Wrapper Folder Support (NEW)

Many learning management systems (including BrightSpace) create ZIP files with a wrapper folder containing all student submissions. This is now automatically detected and handled:

```
submission.zip/
  └── Programming Exam 1/              ← Wrapper folder (auto-detected)
      ├── Student_Name_1/
      │   ├── Main.java
      │   └── Helper.java
      ├── Student_Name_2/
      │   └── script.py
      └── Student_Name_3/
          └── program.cpp
```

**How it works:**
- The system analyzes the ZIP structure on upload
- If all files share a common top-level folder with multiple subfolders, it's treated as a wrapper
- The wrapper folder is automatically stripped away
- Student folders are extracted from one level deeper

This means you can upload BrightSpace downloads directly without needing to repackage them!

### BrightSpace Format

The system supports BrightSpace's default ZIP format with delimiter patterns:

**Simple BrightSpace format:**
```
submission.zip/
  ├── Assignment - John Doe/
  │   ├── Main.java
  │   └── Helper.java
  └── Assignment - Jane Smith/
      └── script.py
```

**BrightSpace with timestamps (most common):**
```
submission.zip/
  └── Programming Exam 1/              ← Wrapper folder
      ├── 10001-500001 - Ada Example - Oct 10, 2025 1022 AM/
      │   ├── Main.java
      │   └── Helper.java
      └── 10002-500002 - Jane Smith - Oct 11, 2025 1130 AM/
          └── script.py
```

**How it works:**
- The system looks for the delimiter pattern ` - ` (space-dash-space) in folder names
- For BrightSpace format `ID - Student Name - Timestamp`, it extracts the **middle part** (Student Name)
- This matches BrightSpace's actual download format from their gradebook system

The system will automatically parse the student name from the folder structure.

### Supported File Types

The following file types are supported for grading:

**Source Code (High Priority):**
- `.java` - Java source files
- `.py` - Python scripts
- `.cpp`, `.c` - C/C++ source files
- `.cs` - C# source files
- `.js`, `.ts` - JavaScript/TypeScript
- `.sas` - SAS programs

**Documents (Medium/Low Priority):**
- `.txt` - Plain text files
- `.md` - Markdown files
- `.docx` - Word documents
- `.pdf` - PDF documents

**Data Files (Low Priority):**
- `.csv` - CSV data files
- `.xlsx`, `.xls` - Excel spreadsheets

### Files Ignored

The following files and directories are automatically ignored:

**Noise Directories:**
- `__MACOSX` - macOS metadata
- `.git`, `.svn`, `.hg` - Version control
- `node_modules` - Node.js dependencies
- `.venv`, `venv` - Python virtual environments
- `__pycache__` - Python cache
- `.idea`, `.vscode` - IDE directories
- `target`, `build`, `dist` - Build artifacts

**Metadata Files:**
- `._*` - macOS resource forks
- `.DS_Store` - macOS folder metadata
- `Thumbs.db` - Windows thumbnails

**Binary Files:**
- `.exe`, `.dll`, `.so` - Executables
- `.class`, `.jar`, `.war` - Java bytecode
- `.zip`, `.tar`, `.gz` - Archives
- `.jpg`, `.png`, `.gif` - Images
- `.mp3`, `.mp4`, `.avi` - Media files

## Token Safety and Budgeting

### Context Window Limits

GPT-5 models have a 128,000 token context window. To prevent `context_length_exceeded` errors, the system implements token budgeting:

- **Input Budget**: 65% of context window (~83,000 tokens)
- **Reserved**: 35% for rubric text, error definitions, and output (~45,000 tokens)

### Token Estimation

Tokens are estimated using a conservative approximation:
- **1 token ≈ 4 characters** (pessimistic to provide safety margin)

For example:
- 1,000 characters ≈ 250 tokens
- 10,000 characters ≈ 2,500 tokens
- 40,000 characters ≈ 10,000 tokens

### File Prioritization

When a student's submission exceeds the token budget, files are prioritized by extension:

1. **Priority 100** (Highest): `.java`, `.py`, `.cpp`, `.c`, `.cs`, `.sas`
2. **Priority 90**: `.js`, `.ts`
3. **Priority 50**: `.txt`, `.md`
4. **Priority 40**: `.xml`, `.json`, `.yaml`
5. **Priority 30**: `.docx`, `.pdf`
6. **Priority 20**: `.html`
7. **Priority 10**: `.csv`, `.xlsx`

Files are included in priority order until the token budget is reached.

### Truncation Behavior

If a student's submission exceeds the token budget:

1. **Highest priority files are included first**
2. **Lower priority files are omitted**
3. **A truncation notice is displayed in the UI**
4. **Omitted files are listed for transparency**

Example truncation notice:
```
⚠️ Submission truncated: 3 file(s) omitted due to token budget (~85,000 tokens)

Files omitted:
  - documentation.pdf
  - test_data.csv
  - notes.txt
```

### Best Practices for Students

To avoid truncation, students should:

1. **Submit only relevant files** (no generated files, dependencies, or media)
2. **Keep individual files reasonably sized** (< 10,000 lines of code per file)
3. **Remove commented-out code** and unnecessary whitespace
4. **Avoid submitting large data files** if not required

### Best Practices for Instructors

To optimize grading with token limits:

1. **Specify file types in assignment instructions** (e.g., "Submit .java files only")
2. **Set file size limits** in assignment requirements
3. **Encourage modular code** with multiple smaller files
4. **Use reference solutions** to show expected file structure

## Async Grading and Concurrency

### Concurrent Processing

Multiple students are graded concurrently using Python's `asyncio.TaskGroup`:

- **Default concurrency**: Up to 5 students graded simultaneously
- **Bounded by OpenAI rate limits**: Automatic retries on rate limit errors
- **Progress tracking**: Each student has an individual status block
- **Error isolation**: Failures for one student don't stop the batch

### Grading Workflow

1. **ZIP Extraction**: Parse ZIP into per-student `StudentSubmission` objects
2. **Token Budgeting**: Apply token limits and file prioritization
3. **Task Creation**: Create async grading tasks for each student
4. **Concurrent Execution**: Execute tasks with `asyncio.TaskGroup`
5. **Results Aggregation**: Collect results and display summary

### Progress Indicators

During grading, the UI shows:

- **Per-student status blocks** (queued → running → complete/error)
- **File lists** for each student
- **Token count estimates** (~X tokens)
- **Truncation warnings** (if applicable)
- **Error messages** with correlation IDs for debugging

### Results Display

After grading completes:

- **Summary table** with scores, percentages, and bands
- **Average score metric** across all students
- **Individual results** in expandable sections
- **Error counts** (major/minor) per student
- **Download buttons** for individual feedback (TODO)

## Error Handling

### Per-Student Failures

If grading fails for a specific student:

1. **Error is logged** with full stack trace
2. **Status block shows error state** (❌ Error: Student_Name)
3. **Correlation ID displayed** for debugging (if available)
4. **Batch continues** for other students

### Common Errors

**`context_length_exceeded`**
- **Cause**: Submission exceeds token budget (even after truncation)
- **Solution**: Further reduce file count or content
- **Prevention**: Better file prioritization, more aggressive truncation

**`OpenAISchemaValidationError`**
- **Cause**: LLM output doesn't match `RubricAssessmentResult` schema
- **Solution**: Automatic retries with same prompt
- **Prevention**: Clear rubric definitions, explicit grading instructions

**`OpenAITransportError`**
- **Cause**: Network/API errors (timeouts, rate limits)
- **Solution**: Automatic retries with exponential backoff
- **Prevention**: None (transient errors)

**`ValidationError`** (Pydantic)
- **Cause**: Invalid rubric configuration or error definitions
- **Solution**: Fix rubric/error definition schemas
- **Prevention**: Validate configurations before grading

## Performance Considerations

### Typical Grading Times

- **Single student**: 10-30 seconds (depends on submission size and model)
- **5 students (concurrent)**: 15-45 seconds (parallelized)
- **20 students**: 1-2 minutes (batched in groups of 5)

### Optimizations

1. **Token budgeting reduces prompt size** → faster inference
2. **Concurrent grading** → better throughput
3. **File prioritization** → most relevant code graded first
4. **Retry logic** → handles transient failures without manual intervention

## Future Enhancements

### Planned Features

- [ ] **Downloadable aggregate reports** (CSV/JSON/ZIP)
- [ ] **Multi-step grading** (per-file summarization → final assessment)
- [ ] **Configurable concurrency limits** (UI setting)
- [ ] **Token usage tracking** (display actual tokens used)
- [ ] **Progress bars** (percentage completion)
- [ ] **Email notifications** (when batch completes)

### Under Consideration

- [ ] **Streaming output** (show grading in real-time)
- [ ] **Chunking for very large files** (split single file across multiple prompts)
- [ ] **RAG/vector store** (for very large codebases)
- [ ] **Custom prioritization rules** (instructor-defined file priorities)

## Troubleshooting

### ZIP Won't Upload

**Symptom**: ZIP file rejected or no students extracted

**Possible causes:**
- ZIP is corrupted or not a valid ZIP file
- No folders in ZIP (files at root level)
- All files have unaccepted extensions
- All files ignored by noise filters

**Solutions:**
1. Verify ZIP structure matches expected format
2. Check accepted file types in assignment
3. Ensure student folders exist
4. Remove noise files before zipping

### Token Budget Exceeded Despite Truncation

**Symptom**: `context_length_exceeded` error even after truncation

**Possible causes:**
- Very large reference solution or instructions
- Too many error definitions
- Rubric description too verbose

**Solutions:**
1. Reduce reference solution size (trim unnecessary code)
2. Reduce assignment instructions (focus on requirements)
3. Disable unused error definitions
4. Simplify rubric criterion descriptions

### Slow Grading Performance

**Symptom**: Batch grading takes longer than expected

**Possible causes:**
- Large submissions (high token counts)
- OpenAI API rate limits
- Network latency

**Solutions:**
1. Encourage students to submit smaller files
2. Wait a few minutes and retry (rate limits are time-based)
3. Contact OpenAI support if consistent slowness

## Support and Feedback

For issues, feature requests, or questions:

- **GitHub Issues**: [cpcc_task_automation/issues](https://github.com/gitchrisqueen/cpcc_task_automation/issues)
- **Documentation**: See `/docs` directory for additional guides
- **Code**: See `src/cqc_cpcc/utilities/zip_grading_utils.py` for implementation details
