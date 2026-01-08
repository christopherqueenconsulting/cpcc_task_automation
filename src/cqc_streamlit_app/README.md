# Streamlit UI Package

Multi-page Streamlit application providing web interface for CPCC Task Automation features.

## Features

### Take Attendance
Automated attendance tracking using student activities (assignments, quizzes, discussions) in BrightSpace. Records attendance in MyColleges and tracking spreadsheet.

### Give Feedback
AI-powered feedback generation for student project submissions using OpenAI GPT models.

### Grade Exam
Automated exam grading with AI-generated error definitions and rubric application.

### Find Student
Search and lookup student information across systems.

### Settings
Configure credentials and preferences:
* [OpenAI API Key](https://platform.openai.com/account/api-keys)
* Instructor User ID
* Instructor Password
* Instructor Signature
* Attendance Tracker URL

## Documentation

For detailed documentation about this package, see:

**[docs/src-cqc-streamlit-app.md](../../docs/src-cqc-streamlit-app.md)**

For overall project documentation:
- [Project README](../../README.md) - Quick start and overview
- [docs/README.md](../../docs/README.md) - Documentation hub
- [ARCHITECTURE.md](../../ARCHITECTURE.md) - System architecture
- [PRODUCT.md](../../PRODUCT.md) - Product features and use cases

## Running the Application

```bash
# From project root
poetry run streamlit run src/cqc_streamlit_app/Home.py
```

Or use the interactive launcher:
```bash
./run.sh
```