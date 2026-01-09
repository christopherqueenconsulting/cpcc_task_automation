# LangChain Legacy Code (Deprecated)

⚠️ **This directory contains deprecated LangChain-based code scheduled for removal.**

## Migration Status

### ✅ Migrated to OpenAI Wrapper
- **Exam Grading**: Now uses `exam_grading_openai.py` by default
- **Project Feedback**: Now uses `openai_client.get_structured_completion()`

### ⏳ Pending Migration
- **Assignment Grading**: `generate_assignment_feedback_grade()` in `chains.py`
  - Still uses LangChain PromptTemplate and chains
  - Should be migrated to OpenAI wrapper

### 🔒 Backward Compatibility
- Legacy code paths preserved with `use_openai_wrapper=False` flag
- Will be removed in future major version

## Files in This Directory

- `chains.py` (500 lines) - LangChain chain builders, prompt templates
- `llms.py` (59 lines) - LangChain LLM configuration helpers
- `prompts.py` (530 lines) - Legacy prompt templates

## Why Deprecated?

LangChain was removed due to:
1. Pydantic v2 compatibility issues
2. Complex retry/parsing logic
3. Maintenance burden
4. Native OpenAI structured outputs are more reliable

## Migration Guide

### Old (LangChain)
```python
from cqc_cpcc.utilities.AI.llm.chains import get_feedback_completion_chain
from cqc_cpcc.utilities.AI.llm.llms import get_default_llm

llm = get_default_llm()
chain, parser, prompt = get_feedback_completion_chain(llm=llm, ...)
result = await chain.ainvoke(...)
```

### New (OpenAI Wrapper)
```python
from cqc_cpcc.utilities.AI.openai_client import get_structured_completion

result = await get_structured_completion(
    prompt="...",
    model_name="gpt-4o",
    schema_model=YourModel,
    temperature=0.2
)
```

## References
- `MIGRATION_NOTES.md` - Project feedback migration details
- `DEPENDENCY_CLEANUP.md` - Dependency changes
- `ARCHITECTURE.md` - System architecture
