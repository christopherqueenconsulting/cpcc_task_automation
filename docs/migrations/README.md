# Migrations

This directory contains migration guides, testing documentation, and notes related to major architectural changes in the project.

## Purpose

These documents help:
- **Guide transitions** between different implementations or architectures
- **Document testing approaches** for complex changes
- **Preserve knowledge** about why and how migrations were performed
- **Assist future refactoring** by showing what worked and what didn't

## Contents

| Document | Description |
|----------|-------------|
| [DEPENDENCY_CLEANUP.md](DEPENDENCY_CLEANUP.md) | Cleanup of unused LangChain dependencies |
| [DUPLICATE_KEY_FIX_TESTING.md](DUPLICATE_KEY_FIX_TESTING.md) | Testing guide for Streamlit duplicate key fixes |
| [MIGRATION_NOTES.md](MIGRATION_NOTES.md) | Project feedback migration from LangChain to OpenAI |
| [MIGRATION_SUMMARY.md](MIGRATION_SUMMARY.md) | Exam grading migration from LangChain to OpenAI |

## Key Migrations

### LangChain → OpenAI Wrapper
The project has progressively migrated from LangChain to direct OpenAI API usage:
- **Exam grading** (MIGRATION_SUMMARY.md) - First major migration
- **Project feedback** (MIGRATION_NOTES.md) - Second migration
- **Dependency cleanup** (DEPENDENCY_CLEANUP.md) - Final cleanup

### Streamlit UI Fixes
- **Duplicate key errors** (DUPLICATE_KEY_FIX_TESTING.md) - Comprehensive testing approach

## Usage

When planning similar migrations or architectural changes:
1. Review relevant migration documents for patterns and pitfalls
2. Check testing strategies used to validate changes
3. Consider backward compatibility approaches taken
4. Learn from challenges encountered

## Note

These documents represent point-in-time snapshots. The codebase has continued to evolve. Always check current implementation for the latest patterns and best practices.
