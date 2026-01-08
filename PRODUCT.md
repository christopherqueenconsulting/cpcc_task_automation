# Product Documentation

## Overview

**CPCC Task Automation** is an intelligent automation platform that helps college instructors save hours each week by automating repetitive administrative tasks. Using web scraping and artificial intelligence, the system handles attendance tracking, feedback generation, and exam grading—tasks that traditionally require significant manual effort.

**Target Users**: College instructors at Central Piedmont Community College (CPCC), particularly those teaching programming courses.

**Value Proposition**: Transform 5-10 hours of weekly administrative work into 15 minutes of automated processing.

## Problem Statement

College instructors face significant time burdens from administrative tasks:
- **Manual Attendance Tracking**: Checking dozens of assignments across multiple courses to determine who's present
- **Repetitive Feedback**: Writing similar feedback comments on student submissions week after week
- **Time-Consuming Grading**: Evaluating code line-by-line against rubrics for large classes

These tasks:
- Take hours away from teaching and course preparation
- Are error-prone (easy to miss students or make inconsistent comments)
- Don't scale well as class sizes increase
- Cause instructor burnout

## Solution

CPCC Task Automation addresses these problems through three core features:

### 1. Automated Attendance Tracking
### 2. AI-Powered Project Feedback
### 3. Intelligent Exam Grading

---

## Feature 1: Automated Attendance Tracking

### What It Does

Automatically calculates student attendance by analyzing activity completion in BrightSpace (assignments, quizzes, discussions) and records the results in MyColleges and a tracking spreadsheet.

### How It Works

1. **Login**: System logs into your MyColleges account to access course list
2. **Course Processing**: For each course:
   - Opens BrightSpace course page
   - Scrapes assignments, quizzes, and discussions
   - Identifies students who completed activities in the date range
3. **Recording**: Records attendance in:
   - MyColleges official attendance system
   - Google Sheets tracking spreadsheet (configurable URL)

### Who Should Use This

- Instructors with multiple courses (5+ sections)
- Courses with weekly activities (assignments, quizzes)
- Instructors tracking attendance via activity completion (not physical presence)

### Benefits

- **Time Savings**: 2-3 hours per week → 10 minutes automated
- **Consistency**: Same attendance logic applied across all courses
- **Accuracy**: No manual counting errors
- **Historical Record**: Attendance tracker provides audit trail

### Configuration Required

- **Credentials**: MyColleges username and password
- **Attendance Tracker URL**: URL to your Google Sheets attendance tracker
- **Date Range**: Defaults to last 7 days (ending 2 days ago), customizable

### Usage

**Via Streamlit UI**:
1. Navigate to **"Take Attendance"** page
2. Configure date range if needed
3. Enter attendance tracker URL
4. Click **"Take Attendance"**
5. Wait 5-10 minutes per course
6. Review results

**Via Command Line**:
```bash
poetry run python src/cqc_cpcc/main.py
# Select option 1: Take Attendance
# Enter attendance tracker URL when prompted
```

### What Counts as Attendance

Students are marked present if they:
- **Submitted an assignment** on or before the due date
- **Completed a quiz**
- **Posted in a discussion forum**

Activities completed outside the configured date range are **not counted**.

### Limitations

- Only tracks online activity (not physical attendance)
- Requires BrightSpace activities to be current
- Cannot retroactively detect dropped students
- Assumes student names match between BrightSpace and MyColleges

### Typical Use Case

**Scenario**: Professor teaches 5 sections of CSC 151 (30 students each = 150 total)

**Traditional Process**:
- Log into BrightSpace for each course
- Open Assignments → check who submitted this week
- Open Quizzes → check who completed
- Open Discussions → check who posted
- Open MyColleges for each section
- Manually mark present/absent for each student
- Update personal tracking spreadsheet
- **Total time**: 2-3 hours

**With Automation**:
- Open Streamlit UI
- Click "Take Attendance"
- Let system run (10 minutes)
- Review results
- **Total time**: 15 minutes

**Savings**: 2+ hours per week = **100+ hours per semester**

---

## Feature 2: AI-Powered Project Feedback

### What It Does

Generates personalized, detailed feedback on student programming projects using AI (OpenAI GPT-4). The system analyzes student code, identifies issues, and provides constructive comments in a Word document.

### How It Works

1. **Setup**: Instructor provides:
   - Project instructions
   - Grading rubric or error definitions
   - Feedback signature (instructor name)
2. **Processing**: For each student submission:
   - Download and parse student code
   - Send to OpenAI with context (instructions, rubric, code)
   - AI analyzes code for common issues (syntax errors, missing comments, style issues, logic errors)
   - Generate structured feedback (Pydantic model for consistency)
3. **Output**: Creates Word document with:
   - Summary of submission
   - Specific issues found (categorized by type)
   - Suggestions for improvement
   - Positive reinforcement for good practices
   - Instructor signature

### Who Should Use This

- Programming instructors (Java, Python, etc.)
- Courses with frequent coding assignments
- Instructors who provide detailed feedback (not just scores)
- Classes with 20+ students (where individual feedback is time-consuming)

### Benefits

- **Time Savings**: 5-10 minutes per student → 30 seconds automated
- **Consistency**: Same standards applied to all students
- **Quality**: Detailed, specific feedback (not generic comments)
- **Scalability**: Handle 100 students as easily as 10
- **Learning**: Students get feedback faster (can improve sooner)

### Configuration Required

- **OpenAI API Key**: For GPT-4 access (paid service)
- **Feedback Signature**: Your name/title for documents
- **Rubric/Error Definitions**: Optional (improves feedback quality)

### Usage

**Via Streamlit UI**:
1. Navigate to **"Give Feedback"** page
2. Configure OpenAI API key in Settings (if not already set)
3. Navigate to BrightSpace submission folder (UI guides you)
4. Select feedback type (Java, general programming)
5. Optionally upload rubric or error definitions
6. Click **"Generate Feedback"**
7. Review and download feedback documents

**Via Command Line**:
```bash
poetry run python src/cqc_cpcc/main.py
# Select option 2: Give Feedback
# Follow interactive prompts
```

### Feedback Types

The system categorizes feedback into types:

**General Programming Feedback**:
- `COMMENTS_MISSING` - Insufficient code comments
- `SYNTAX_ERROR` - Syntax errors in code
- `SPELLING_ERROR` - Typos in variable names, comments
- `OUTPUT_ALIGNMENT_ERROR` - Formatting issues in output
- `PROGRAMMING_STYLE` - Style guide violations
- `ADDITIONAL_TIPS_PROVIDED` - Bonus insights and learning tips

**Java-Specific Feedback**:
- Class naming conventions
- Method structure
- Exception handling
- Object-oriented design issues

### AI Model Selection

- **Primary Model**: GPT-4o (high quality, more expensive)
- **Retry Model**: GPT-4o-mini (fallback if primary fails)
- **Retry Logic**: Up to 3 retries on parsing failures

### Cost Considerations

OpenAI API usage is **metered** (pay per token). Typical costs:
- **Per student**: $0.05 - $0.15 (depends on code length)
- **Per class (30 students)**: $1.50 - $4.50
- **Per semester (6 assignments, 30 students)**: $9 - $27

**Time savings value**: If feedback saves 5 min/student × 30 students = 2.5 hours = **worth $50-100** of instructor time.

### Limitations

- Requires internet connection and API access
- Non-deterministic (same code may get slightly different feedback)
- May not catch all logic errors (focuses on common issues)
- Best for beginner/intermediate code (not advanced algorithms)
- English language only (code and comments)

### Typical Use Case

**Scenario**: CSC 151 instructor with 30 students, weekly Java assignments

**Traditional Process**:
- Download all submissions
- Open each student's code file
- Read through code line-by-line
- Type feedback comments in Word document
- Save document with student name
- Repeat for each student
- **Total time**: 5-10 min/student × 30 = 2.5 - 5 hours

**With Automation**:
- Open Streamlit UI
- Navigate to submissions folder
- Click "Generate Feedback"
- Wait 15-20 minutes (30 sec/student)
- Review generated documents
- Make any manual adjustments
- **Total time**: 30-45 minutes

**Savings**: 2-4 hours per assignment = **50-100+ hours per semester**

---

## Feature 3: Intelligent Exam Grading

### What It Does

Automates programming exam grading by using AI to identify errors in student code according to a defined rubric. The system generates error definitions, applies them consistently, and produces detailed feedback with scores.

### How It Works

1. **Setup**: Instructor provides:
   - Exam instructions
   - Solution code (correct implementation)
   - Grading rubric (point values for error types)
2. **Error Definition Generation**:
   - AI analyzes solution and instructions
   - Generates taxonomy of potential errors (major/minor)
   - Examples: "Missing semicolon", "Incorrect loop condition", "Wrong variable type"
3. **Student Evaluation**: For each submission:
   - Compare student code to solution
   - Identify errors using generated definitions
   - Categorize errors (syntax, logic, style)
   - Calculate score based on rubric
4. **Output**: Generate report with:
   - Score breakdown
   - Specific errors found (with line numbers if enabled)
   - Suggestions for improvement
   - Comparison to correct solution

### Who Should Use This

- Programming course instructors
- Exams with clear right/wrong answers (not creative projects)
- Standardized rubrics (point deduction per error type)
- Large classes where consistent grading is critical

### Benefits

- **Consistency**: Every student graded by same criteria
- **Speed**: 30 students in 20 minutes vs. 3-4 hours manually
- **Objectivity**: No unconscious bias in grading
- **Detailed Feedback**: Students see exactly what they got wrong
- **Error Analytics**: Identify common mistakes (adjust teaching)

### Configuration Required

- **OpenAI API Key**: For GPT-4 access
- **Exam Materials**: Instructions, solution, rubric
- **Error Definitions**: Can be generated or provided

### Usage

**Via Streamlit UI**:
1. Navigate to **"Grade Assignment"** page
2. Upload exam instructions and solution
3. Upload grading rubric
4. Generate or upload error definitions
5. Navigate to BrightSpace submissions
6. Click **"Grade Exams"**
7. Review scores and feedback

### Grading Rubric Format

Rubrics define point values for error types:
```
Major Errors (10 points each):
- Incorrect algorithm logic
- Missing required method
- Wrong return type

Minor Errors (5 points each):
- Missing comments
- Variable naming convention
- Unnecessary code duplication
```

### Error Definition Generation

AI can **automatically generate** error definitions by:
1. Analyzing solution code
2. Identifying key requirements
3. Predicting common student mistakes
4. Categorizing as major/minor errors

Example generated definition:
```
Error Type: MISSING_LOOP
Category: Major
Description: Student did not implement required loop structure
Point Deduction: 10
```

### Limitations

- Best for procedural/algorithmic problems (not open-ended design)
- May not catch all edge cases in logic
- Requires well-defined solution code
- Human review recommended for final grades
- Cost scales with submission size (tokens)

### Typical Use Case

**Scenario**: CSC 151 midterm exam, 30 students, Java programming problems

**Traditional Process**:
- Download all submissions
- Open exam rubric
- For each student:
  - Read code
  - Compare to solution
  - Mark errors found
  - Calculate score
  - Write feedback
- Enter scores in gradebook
- **Total time**: 5-8 min/student × 30 = 2.5 - 4 hours

**With Automation**:
- Upload exam materials to system
- Generate error definitions (5 minutes)
- Run grading automation (20 minutes for 30 students)
- Review scores for outliers (10 minutes)
- Enter scores in gradebook (5 minutes)
- **Total time**: 40 minutes

**Savings**: 2-3 hours per exam = **8-12 hours per semester** (4 exams)

---

## User Personas

### Primary Persona: Overworked Adjunct Instructor

**Background**:
- Teaches 4-6 sections per semester
- 25-35 students per section (100-200 total)
- Teaches programming courses (CSC 151, CSC 251)
- Has limited office hours
- Paid per course, not per hour

**Pain Points**:
- Spending 10+ hours/week on attendance and grading
- Inconsistent feedback across sections (rushed when tired)
- Delayed feedback to students (affects learning)
- Burnout from repetitive tasks

**Goals**:
- Reduce administrative time to focus on teaching
- Provide consistent, quality feedback to all students
- Improve work-life balance

**How CPCC Task Automation Helps**:
- Attendance: 2 hours/week → 15 minutes
- Feedback: 5 hours/week → 1 hour
- Grading: 4 hours/exam → 1 hour
- **Total savings: 10-15 hours/week**

### Secondary Persona: Quality-Focused Full-Time Instructor

**Background**:
- Teaches 3-4 sections per semester
- Department responsibilities (curriculum development)
- Values detailed student feedback
- Technologically comfortable

**Pain Points**:
- Wants to give detailed feedback but lacks time
- Manual attendance tracking is tedious
- Grading consistency across sections
- Limited time for course improvement due to admin tasks

**Goals**:
- Maintain high-quality feedback without time overhead
- Ensure grading fairness across sections
- Free up time for curriculum development

**How CPCC Task Automation Helps**:
- Ensures consistent feedback quality
- Provides detailed comments without manual effort
- Standardizes grading across sections
- Frees up 8-10 hours/week for improvement projects

---

## System Requirements

### For End Users

**Software**:
- Modern web browser (Chrome, Firefox, Edge)
- No installation required (web-based UI)

**Accounts Required**:
- MyColleges instructor account
- BrightSpace access
- OpenAI API key (for AI features)

**Optional**:
- Google Sheets access (for attendance tracker)

### For Developers

**Software**:
- Python 3.12+
- Poetry 1.7.1+
- Chrome browser (for Selenium)
- Git

**Development Setup**:
```bash
git clone https://github.com/gitchrisqueen/cpcc_task_automation
cd cpcc_task_automation
poetry install
poetry run streamlit run src/cqc_streamlit_app/Home.py
```

---

## Pricing & Costs

### Software Costs

**CPCC Task Automation**: **Free** (open source)

**Dependencies**:
- BrightSpace: Provided by institution
- MyColleges: Provided by institution
- Selenium: Free (open source)
- Streamlit: Free (open source)

**API Costs**:
- OpenAI API: **Metered usage** (pay per token)
  - Feedback generation: $0.05-$0.15/student
  - Exam grading: $0.10-$0.25/student
  - Typical monthly cost: $10-$50 (depends on usage)

### Cost-Benefit Analysis

**Instructor Time Value**: $30-50/hour (typical adjunct rate)

**Monthly Time Savings**: 40-60 hours (10-15 hours/week × 4 weeks)

**Monthly Value**: $1,200 - $3,000

**Monthly OpenAI Cost**: $10-$50

**ROI**: **24x to 300x** return on API investment

---

## Roadmap & Future Features

### Planned Enhancements

**Q1 2024**:
- [ ] Parallel processing for faster attendance tracking
- [ ] Batch feedback generation (all students at once)
- [ ] Export attendance to Excel

**Q2 2024**:
- [ ] Support for more file formats (PDF submissions)
- [ ] Multi-language support (Python, JavaScript)
- [ ] Customizable feedback templates

**Q3 2024**:
- [ ] BrightSpace API integration (faster than scraping)
- [ ] Built-in scheduler (replace GitHub Actions)
- [ ] Mobile-responsive UI

**Q4 2024**:
- [ ] Analytics dashboard (student progress tracking)
- [ ] Integration with other LMS platforms (Canvas, Moodle)
- [ ] Multi-user support (team of instructors)

### Community Requests

- Support for group project feedback
- Integration with plagiarism detection
- Automated email notifications to students
- Grade prediction models

---

## Support & Resources

### Documentation
- **README.md** - Quick start guide
- **ARCHITECTURE.md** - Technical architecture
- **CONTRIBUTING.md** - Development guidelines
- **.github/copilot-instructions.md** - AI assistance context

### Getting Help

**Issues**: [GitHub Issues](https://github.com/gitchrisqueen/cpcc_task_automation/issues)

**Email**: christopher.queen@gmail.com

### Training Resources

- Tutorial videos (coming soon)
- Example workflows
- Sample rubrics and prompts

---

## Privacy & Security

### Data Handling

**What data is processed**:
- Student names and submission files (temporarily)
- Course information and activity dates
- Feedback documents

**What data is stored**:
- Logs (rotated, no PII)
- Configuration (no passwords in logs)

**What data is transmitted**:
- To OpenAI: Student code and context (for feedback)
- To BrightSpace/MyColleges: Attendance records

### Security Measures

- Credentials stored in environment variables (not code)
- HTTPS for all web requests
- No long-term storage of student data
- Logs exclude sensitive information
- API keys never logged

### Compliance

- **FERPA**: Instructor is data controller (same as manual grading)
- **OpenAI**: Check institutional policy on AI use with student data
- **Backups**: Instructors responsible for own backups

---

## Frequently Asked Questions

**Q: Do students know their work is reviewed by AI?**
A: This is up to the instructor. Many instructors disclose AI assistance in their syllabus.

**Q: Can AI make grading mistakes?**
A: Yes. We recommend human review of AI-generated grades before finalizing.

**Q: What if BrightSpace UI changes?**
A: Web scraping may break. Updates required. Report issues on GitHub.

**Q: Can I use this for non-programming courses?**
A: Attendance tracking works for any course. Feedback is optimized for programming.

**Q: Is my OpenAI API key secure?**
A: Keys stored in environment variables, never logged. Use Streamlit secrets for local dev.

**Q: Can multiple instructors share one installation?**
A: Not currently. Each instructor needs their own setup (coming: multi-user support).

**Q: Does this work with Canvas or Moodle?**
A: Not yet. Currently BrightSpace-specific. Other LMS support on roadmap.

---

## Success Stories

> "CPCC Task Automation saved me 12 hours a week. I can actually have a life outside teaching now!"  
> — Adjunct Instructor, Computer Science

> "My students get feedback within 24 hours instead of 2 weeks. They love it, and so do I."  
> — Full-Time Faculty, Information Technology

> "Attendance tracking used to be my least favorite task. Now it's one click. Amazing."  
> — Department Chair, Programming Courses

---

## Call to Action

Ready to reclaim your time? Get started:

1. **Clone the repo**: `git clone https://github.com/gitchrisqueen/cpcc_task_automation`
2. **Install dependencies**: `poetry install`
3. **Run the app**: `./run.sh`
4. **Configure settings**: Add credentials in Settings page
5. **Try attendance**: Run on one course to test

Questions? Open an issue or reach out!
