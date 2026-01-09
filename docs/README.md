# CPCC Task Automation - Documentation Hub

This directory contains comprehensive technical documentation for the CPCC Task Automation project.

## Purpose

This documentation provides in-depth information about the architecture, components, and implementation details of the system. It is intended for developers, contributors, and anyone seeking to understand how the system works internally.

## Where to Start

- **New to the project?** Start with the [root README.md](../README.md) for a quick overview and setup instructions.
- **Want to understand the architecture?** Read [ARCHITECTURE.md](ARCHITECTURE.md) for system design and data flows.
- **Want to know about features?** See [PRODUCT.md](PRODUCT.md) for detailed feature descriptions and use cases.
- **Ready to contribute?** Check [CONTRIBUTING.md](CONTRIBUTING.md) for development guidelines.

## Documentation Index

### Core Documentation

| Document | Description |
|----------|-------------|
| [../README.md](../README.md) | Project overview, quick start, and basic usage |
| [ARCHITECTURE.md](ARCHITECTURE.md) | System architecture, design decisions, and data flows |
| [PRODUCT.md](PRODUCT.md) | Product features, user personas, and roadmap |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Development guidelines and contribution process |
| [TESTING.md](TESTING.md) | Testing guide and best practices |

### Module Documentation

| Document | Description |
|----------|-------------|
| [src-cqc-cpcc.md](src-cqc-cpcc.md) | Core automation package (attendance, feedback, grading) |
| [src-cqc-streamlit-app.md](src-cqc-streamlit-app.md) | Streamlit web UI and multi-page application |
| [utilities.md](utilities.md) | Shared utility modules (Selenium, date handling, logging) |
| [ai-llm.md](ai-llm.md) | AI/LLM integration (LangChain, OpenAI, prompts, chains) |
| [testing.md](testing.md) | Testing strategy, test structure, and how to run tests |

### GitHub Copilot Instructions

| Document | Description |
|----------|-------------|
| [../.github/copilot-instructions.md](../.github/copilot-instructions.md) | Main Copilot context and coding standards |
| [../.github/instructions/](../.github/instructions/) | Path-specific instructions for different code areas |

## Documentation Structure

```
cpcc_task_automation/
├── README.md                  # Project overview and quick start
├── docs/                      # Detailed technical documentation
│   ├── README.md             # This file - documentation hub
│   ├── ARCHITECTURE.md       # System architecture
│   ├── PRODUCT.md            # Product documentation
│   ├── CONTRIBUTING.md       # Development guidelines
│   ├── TESTING.md            # Testing documentation
│   ├── src-cqc-cpcc.md       # Core automation package docs
│   ├── src-cqc-streamlit-app.md # UI package docs
│   ├── utilities.md          # Utility modules docs
│   ├── ai-llm.md             # AI/LLM integration docs
│   ├── codecov_enforcement.md # Coverage requirements
│   ├── pr-summaries/         # Historical PR summaries
│   ├── migrations/           # Migration notes and guides
│   └── notes/                # Development notes
└── .github/
    ├── copilot-instructions.md # Copilot context
    └── instructions/           # Path-specific instructions
```

## Key Concepts

### Core Automation (`src/cqc_cpcc/`)
The heart of the system - contains all automation logic for attendance tracking, feedback generation, and exam grading. Uses Selenium for web scraping and LangChain for AI integration.

### Streamlit UI (`src/cqc_streamlit_app/`)
Multi-page web interface built with Streamlit. Provides user-friendly access to all automation features with forms, file uploads, and progress indicators.

### Utilities (`src/cqc_cpcc/utilities/`)
Shared utility modules used across the application - Selenium helpers, date calculations, logging, environment configuration, and custom parsers.

### AI/LLM (`src/cqc_cpcc/utilities/AI/`)
LangChain integration for AI-powered features. Includes LLM configuration, prompt templates, chain construction, and custom Pydantic parsers.

## Common Tasks

### Understanding a Feature
1. Read the feature description in [PRODUCT.md](PRODUCT.md)
2. Check the architecture overview in [ARCHITECTURE.md](ARCHITECTURE.md)
3. Review the module documentation for implementation details
4. Look at the source code with context from the docs

### Adding a New Feature
1. Review [CONTRIBUTING.md](CONTRIBUTING.md) for development guidelines
2. Check [ARCHITECTURE.md](ARCHITECTURE.md) for design patterns
3. Study similar existing features in the module docs
4. Follow the coding standards in [../.github/copilot-instructions.md](../.github/copilot-instructions.md)

### Debugging Issues
1. Check logs in the `logs/` directory
2. Review [utilities.md](utilities.md) for logging and error handling patterns
3. Check [testing.md](testing.md) for how to run relevant tests
4. Review module documentation for known limitations

### Modifying AI Features
1. Read [ai-llm.md](ai-llm.md) to understand the AI architecture
2. Check existing prompts in `src/cqc_cpcc/utilities/AI/llm/prompts.py`
3. Review chain construction patterns
4. Test with various inputs (LLMs are non-deterministic)

## Documentation Conventions

### Code Examples
All code examples use Python 3.12+ syntax and assume the project environment is set up via Poetry.

### File Paths
File paths are relative to the repository root unless otherwise specified.

### Module References
When referencing code, we use Python import paths:
- `cqc_cpcc.attendance` → `src/cqc_cpcc/attendance.py`
- `cqc_streamlit_app.Home` → `src/cqc_streamlit_app/Home.py`

### External Links
Links to external documentation (Selenium, LangChain, OpenAI) point to the latest stable versions when possible.

## Keeping Documentation Updated

When making code changes:

1. **Update relevant module docs** if you change public APIs or behavior
2. **Update ARCHITECTURE.md** if you change system design or add new components
3. **Update PRODUCT.md** if you add/modify user-facing features
4. **Update this index** if you add new documentation files

## Contributing to Documentation

Documentation improvements are always welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for:
- Documentation standards
- How to submit documentation changes
- Review process

## Questions?

- **GitHub Issues**: [Report issues or ask questions](https://github.com/gitchrisqueen/cpcc_task_automation/issues)
- **Email**: christopher.queen@gmail.com

---

*Last updated: 2026-01-08*
