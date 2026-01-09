# DEPRECATED: LangChain-based implementations
# 
# This module contains legacy LangChain code that is scheduled for removal.
# It is maintained for backward compatibility only.
#
# Migration Status:
# - Exam Grading: ✅ Migrated to OpenAI wrapper (use_openai_wrapper=True by default)
# - Project Feedback: ✅ Migrated to OpenAI wrapper 
# - Assignment Grading: ⏳ Still uses LangChain (pending migration)
#
# New code should use:
# - src/cqc_cpcc/utilities/AI/openai_client.py (get_structured_completion)
# - src/cqc_cpcc/utilities/AI/exam_grading_openai.py
#
# See MIGRATION_NOTES.md and DEPENDENCY_CLEANUP.md for details.
