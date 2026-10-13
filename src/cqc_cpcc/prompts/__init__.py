#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Live LLM prompt text that is not built by a function next to its call site.

Every live prompt is registered in ``src/cqc_cpcc/config/prompt_registry.json`` and
fingerprinted by ``cqc_cpcc.model_eval.prompt_registry``. Changing the text changes the
fingerprint, which must come with a version bump (see ``docs/PROMPTS.md``).
"""
