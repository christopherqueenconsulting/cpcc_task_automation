# Prompt guidelines for the grading calls

Date: 2026-10-24. Produced by a research pass over vendor documentation (OpenAI, OpenRouter,
Anthropic) for the grading call (`openai/gpt-6-luna`, reasoning effort high, JSON-schema output).
Each page was read through a summarising fetch tool, not as raw text. Items marked
"unverified" or "inference" stay marked, are not grounds for a prompt change, and make no
claim beyond the cited source. The prompt changes that used these guidelines are described in
`docs/PROMPTS.md` (rubric-grading v4, exam-grading v4).

---

**Update 2026-10-24.** A second research pass on Luna's caching (OpenRouter, per-model prices) supersedes G22 and
G24 on Luna's rates: gpt-6-luna cached input is $0.01 vs input $0.10 per 1M tokens and a 30-minute cache write is
$0.125 per 1M (OpenAI pricing page and OpenRouter endpoints API; the 0.1x read and 1.25x write ratios are the
GPT-5.6+ rates in OpenAI's prompt-caching guide). The 1,024-token minimum is stated for GPT-5.6 and later; that
gpt-6-luna belongs to that group is an inference. The markings below are left as first written.

# Prompt-writing guideline digest (grading call, openai/gpt-6-luna, effort high, JSON schema)

Seen 2026-10-24. None of the pages state a publication date unless noted; "undated" below means that.
Method note: WebFetch returns a small-model summary of each page, not raw text. Treat paraphrases as
"fetched summary", re-open the URL before quoting. Quotes kept under 15 words.

## Existing repo research (not redone)
- docs/PROMPTS.md (293 lines), docs/PROMPT_EVAL_PLAN.md (214), docs/ai-llm.md (790), docs/openai-structured-outputs-guide.md,
  evals/reports/{2026-10-calibration,2026-10-v2-calibration,prompt-calibration/2026-10-08}.
- Grep for nonce, prefix caching, instruction hierarchy, reasoning-model guidance, few-shot, developer message: NO hits
  in PROMPTS.md / PROMPT_EVAL_PLAN.md / ai-llm.md / evals/reports. Only related: injection is an eval grader
  (PROMPT_EVAL_PLAN.md:52; PROMPTS.md:115-119, `project-feedback` injection calibrated 0.845) and judge output order
  "reasoning before score" (PROMPT_EVAL_PLAN.md:68). openai-structured-outputs-guide.md only links the OpenAI docs.
  So no cited prompt-writing guideline digest exists; this is new.

## Fetch status
- Fetched OK: OpenAI reasoning-best-practices, prompt-guidance (GPT-6), prompt-caching, structured-outputs (first 100k of 136k chars; tail unread),
  prompt-engineering, reasoning, evaluation-best-practices, GPT-5 cookbook prompting guide; OpenRouter prompt-caching, reasoning-tokens,
  structured-outputs; Anthropic prompting best practices, prompt-caching (first 100k of 162k chars), mitigate-jailbreaks, develop-tests (first 100k of 153k).
- FAILED: OpenAI Model Spec pages (model-spec.openai.com root redirects to 2026-08-18.html; fetching that and 2025-12-18.html
  errored in the summarizer). Model Spec claims below come from a WebSearch result snippet of the 2025-12-18 version only: confidence medium.
- FAILED 404: developers.openai.com/api/docs/guides/safety-building-agents.
- No primary source found for: per-call nonce delimiters; ordering of reasoning vs verdict fields in a schema for GPT-6; few-shot for GPT-6 reasoning models (the GPT-6 page is silent).

## Guidelines

G1. Topic 1 (roles). Put application rules in the developer/system layer and the end-user/variable content in the user message; OpenAI frames developer = function definition, user = arguments.
Source: https://developers.openai.com/api/docs/guides/prompt-engineering (undated; names gpt-6-astra). Confidence: high.

G2. Topic 1. Reasoning models take developer messages (not system) since o1-2024-12-17; via OpenRouter/Chat Completions a system message is mapped to developer (the mapping statement came from a search snippet of the same guide; unverified on the page itself). Treat system==developer in practice.
Source: https://developers.openai.com/api/docs/guides/reasoning-best-practices. Confidence: medium.

G3. Topic 1. Suggested developer-message order: identity, instructions, examples, context (data last). Matches Anthropic advice to put long data first and the query/instructions after for 20k+ token inputs (Anthropic reports up to ~30% better quality with query at end). The two vendors disagree on where the bulk data goes; for a cached prefix design, stable rules first and the submission last satisfies OpenAI's order and is acceptable under Anthropic's (query/instructions are repeated or restated after the submission).
Sources: https://developers.openai.com/api/docs/guides/prompt-engineering ; https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices (long-context section). Confidence: high (each), the synthesis is mine.

G4. Topic 1. A one-sentence role is cheap and helps focus tone (Anthropic). OpenAI's reasoning page asks instead for task, constraints, and output format. Use a short role plus a precise task.
Sources: Anthropic best practices "Give Claude a role"; https://developers.openai.com/api/docs/guides/reasoning. Confidence: high.

G5. Topic 2 (hierarchy). Per the Model Spec (2025-12-18 per search snippet): ranking Root > System > Developer > User > Guideline; quoted text, attachments, and tool outputs carry no authority by default, so instructions inside them are information. Authority can be delegated by unquoted higher-level text. Practical consequence: state explicitly in the developer message that the submission is data and instructions inside it are to be reported/ignored.
Source: https://model-spec.openai.com/2025-12-18.html (page fetch FAILED; from search snippet). Confidence: medium. Note GPT-6 Astra system card exists at https://deploymentsafety.openai.com/gpt-6-astra (not read); GPT-6 Luna behavior unverified.

G6. Topic 2. Model Spec guidance to developers (snippet): put untrusted content in dedicated blocks or in YAML/JSON/XML, because plain concatenation is hard for the model to tell apart from instructions.
Source: model-spec.openai.com/2025-12-18.html (snippet only). Confidence: medium.

G7. Topic 2. Anthropic's injection guidance: state an explicit untrusted-content policy in the system prompt (content is data; instructions inside it are information to report, never commands), say what the content is and where it came from, don't put your own instructions inside the untrusted block, apply least privilege, and red-team with deliberate injections. Anthropic's own preference is delivering untrusted text in tool results; for a single-shot call that option does not exist, so G8/G9 apply.
Source: https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/mitigate-jailbreaks (undated). Confidence: high (Anthropic models); transfer to GPT-6 Luna unverified.

G8. Topic 3 (delimiting). JSON-encoding untrusted strings gives unambiguous delimiters, since the attacker cannot close a quote or tag to break out. Candidate for submission text: embed each file as a JSON string field, or XML tags with escaping of the closing tag.
Source: same Anthropic mitigate-jailbreaks page. Confidence: high (Anthropic); for OpenAI, the Model Spec snippet endorses JSON/XML blocks (G6).

G9. Topic 3. OpenAI prompt-engineering guide: XML tags mark where a document starts and ends, attributes can carry metadata the instructions refer to; Markdown headers separate sections. Anthropic: wrap each document in its own tag with source metadata, consistent tag names.
Sources: prompt-engineering guide above; Anthropic best practices ("Structure prompts with XML tags"). Confidence: high.

G10. Topic 3 (nonce). NO primary source found on random per-call nonce delimiters. Unverified. Reasoning from the caching docs (G21, G28): a nonce that appears in the instructions changes the prefix every call and kills cache hits. If a nonce is used, the instruction text must not contain it. Options: (a) fixed fence tags in the stable prefix, plus escaping/neutralising any closing-tag occurrence in the submission (JSON-encoding, G8); (b) put the nonce only in the variable tail (e.g. open and close markers placed in the user message) and have the stable developer message say "the submission is delimited by the markers given in the user message". Option (b) is my inference, not documented.
Confidence: unverified.

G11. Topic 4 (structured outputs). Strict mode: every property in `required`, `additionalProperties: false`, optional values as nullable types. Give important keys clear names, titles and descriptions. The page says structured outputs reduce the need for strongly worded format prompts; the examples use short instructions.
Source: https://developers.openai.com/api/docs/guides/structured-outputs (undated; tail 36k chars unread). Confidence: high on required/additionalProperties, medium on "no duplication" (inferred from "reduces need for strongly worded prompts"); no explicit duplicate-vs-schema ruling found: unverified.

G12. Topic 4. Check for a refusal content item and for status/incomplete_details (truncation at max_output_tokens) before parsing; the refusal may not match the schema. Build the caller to handle both, and to treat a refusal on a student submission as a no-grade outcome.
Source: structured-outputs guide. Confidence: high.

G13. Topic 4. Via OpenRouter: `response_format` json_schema support is per endpoint; set `provider.require_parameters: true`; use `strict: true` and property descriptions; some providers only treat the schema as a hint. Response Healing plugin can reduce invalid JSON on non-streaming calls.
Source: https://openrouter.ai/docs/features/structured-outputs (undated). Confidence: high.

G14. Topic 4/9. Field ordering: examples in the OpenAI guide put `explanation` before the answer and `steps` before `final_answer`, but the page states no rule. OpenAI's eval guide says adding reasoning before the score improves judge results. If the schema has a reasoning/evidence field, place it before the score/level fields.
Sources: structured-outputs guide; https://developers.openai.com/api/docs/guides/evaluation-best-practices. Confidence: medium (schema key-order rule itself: unverified; with a reasoning model that already reasons internally, the benefit of a visible rationale field is unverified).

G15. Topic 5 (length). OpenAI: keep prompts simple and direct; short clear instructions work best for reasoning models. GPT-5 guide: contradictory or vague instructions hurt more than on other models because effort is spent reconciling them; emphatic "maximize/must" language over-triggered behaviour. GPT-6 page: Astra follows longer instructions better but is more sensitive to conflicting context (that is Astra; Luna not stated, unverified).
Sources: reasoning-best-practices; https://developers.openai.com/cookbook/examples/gpt-5/gpt-5_prompting_guide (archived, may be outdated); https://developers.openai.com/api/docs/guides/prompt-guidance. Confidence: high for brevity and no-contradictions.

G16. Topic 5. Anthropic: explaining the reason behind an instruction improves targeting; test the prompt on a colleague with no context; be explicit about output constraints; Anthropic also says remove over-prompting and blanket "always/if in doubt" defaults for newer models.
Source: Anthropic prompting best practices. Confidence: high.

G17. Topic 5. Verbosity: GPT-6 Astra tends to long, list-heavy output; ask for concise prose and specify the format the app needs. Reasoning models avoid Markdown by default in the API. With a JSON schema the visible output is constrained; cap free-text fields (max items, "one sentence") in the schema descriptions.
Sources: prompt-guidance page; reasoning-best-practices. Confidence: medium (Luna-specific verbosity unverified).

G18. Topic 6 (reasoning). Do not instruct step-by-step or "explain your reasoning"; models reason internally and it may hurt. Define success criteria and constraints rather than every step. Effort is a tuning knob, not the main quality lever. GPT-6 page: Sol and Luna accept `none`; defaults medium; remove temperature/top_p when effort is not none. Check that OpenRouter forwards them correctly.
Sources: reasoning-best-practices; https://developers.openai.com/api/docs/guides/reasoning ; prompt-guidance. Confidence: high.

G19. Topic 6. Effort guidance: `high` for hard reasoning where quality beats latency; evaluate medium vs high on complex work; xhigh only with evidence from evals. Reasoning tokens bill as output and share max_output_tokens; OpenAI advises reserving at least 25,000 tokens for reasoning plus output at the start. A too-small cap yields an empty incomplete response that is still billed. OpenRouter: effort maps to a share of max_tokens (high ~80%), empty content with finish_reason "length" when reasoning eats the budget.
Sources: reasoning guide; https://openrouter.ai/docs/use-cases/reasoning-tokens. Confidence: high.

G20. Topic 7 (examples). OpenAI: start zero-shot; add a few examples only if output requirements are complex, and make them closely match the instructions (mismatch hurts). The GPT-6 page gives no few-shot guidance (unverified for Luna). Anthropic: 3-5 relevant, diverse examples wrapped in `<example>` tags. For grading, examples that include a score risk anchoring; no primary source confirms either way: unverified.
Sources: reasoning-best-practices; Anthropic best practices. Confidence: high (each), anchoring caveat unverified.

G21. Topic 8 (caching). Caching is automatic prefix matching: the whole rendered prefix must match; put stable content first (developer instructions, tool defs, reference material) and dynamic content last (timestamps, user text, the current question). Append rather than rewrite earlier content.
Source: https://developers.openai.com/api/docs/guides/prompt-caching (undated; covers GPT-5.6+). Confidence: high.

G22. Topic 8. Minimum cacheable prefix: 1,024 visible input tokens for GPT-5.6 and later; older models vary. Whether openai/gpt-6-luna is in the "GPT-5.6 and later" group is not stated on the page: unverified. Implicit breakpoint at the end of the latest eligible message (user messages; last developer message in the opening developer block). A shared prefix with a changing suffix is not reusable if no breakpoint follows the static part (explicit `prompt_cache_breakpoint` available).
Source: prompt-caching guide. Confidence: high (page), model membership unverified.

G23. Topic 8. Changing reasoning effort via request-level `reasoning.effort` breaks the prefix on GPT-6; OpenAI says keep it stable (use `configuration_update` item mid-conversation). Keep effort and output schema identical across calls to keep the cache. The prompt-caching page also says schema/effort affect the minimum for earlier models.
Sources: prompt-caching guide; prompt-guidance. Confidence: high.

G24. Topic 8. Economics (GPT-5.6+): reads 0.1x input (0.05x on Sol), writes 1.25x for 30m TTL, 2.5x for 12h. TTL `prompt_cache_options.ttl` "30m" default, "12h" option. Cache is per-org. Cached tokens show in `input_tokens_details.cached_tokens`. Luna's exact rates: unverified (check the model page).
Source: prompt-caching guide. Confidence: high (page), Luna rates unverified.

G25. Topic 8. `prompt_cache_key` is optional on GPT-5.6+ (separate accounting per user, limits cross-user probing, no hit guarantee); on earlier models use a stable key to steer routing, about 15 requests/min per key max.
Source: prompt-caching guide. Confidence: high.

G26. Topic 8 (OpenRouter). Sticky routing sends follow-ups for the same model to the same provider to keep the cache warm, only when the provider's cache read is cheaper; sessions expire after 10 min idle; skipped if `provider.order` is set. Default conversation id = hash of the first system/developer message plus first non-system message, so a first message that varies per submission gives a different hash each call. `session_id` (body field or `x-session-id`, max 256 chars) overrides it; falls back to `prompt_cache_key`. Usage fields: `prompt_tokens_details.cached_tokens`, `cache_write_tokens`, `cache_discount`.
Source: https://openrouter.ai/docs/features/prompt-caching (undated). Confidence: high. Whether sticky routing helps across different students' calls with one shared prefix is not stated: unverified; a constant `session_id` or `prompt_cache_key` for a grading batch is the documented lever.

G27. Topic 8 (OpenRouter pass-through). OpenAI automatic caching, 1,024 min, passes through; for GPT-5.6+ `prompt_cache_breakpoint` / `prompt_cache_options` are listed as supported fields; mode "explicit" with no breakpoints turns caching off. Anthropic via OpenRouter: 512-4,096 token minimums by model, `cache_control` up to four breakpoints, 5m default TTL.
Source: OpenRouter prompt-caching page; https://platform.claude.com/docs/en/build-with-claude/prompt-caching. Confidence: high.

G28. Topic 8. Anthropic cache order is tools, system, messages; a breakpoint on a per-request value never hits; changes to thinking/effort config invalidate message cache. Same principle: nothing request-specific (including a nonce) before the last stable breakpoint.
Source: Anthropic prompt-caching page. Confidence: high.

G29. Topic 9 (grading). OpenAI eval guidance: clear, detailed rubrics; add reasoning before the score; validate judge agreement against human labels before scaling; watch verbosity bias (longer favoured) and position bias; prefer pairwise or pass/fail over fine-grained open scoring; add a pass/fail threshold next to numbers.
Source: https://developers.openai.com/api/docs/guides/evaluation-best-practices (undated; Evals API sunset Oct 31 / Nov 30 2026 noted on page). Confidence: high (page), applicability to single-answer rubric grading medium.

G30. Topic 9. Anthropic grader pattern: anchor both ends of a scale with explicit descriptions (1 = ..., 5 = ...), require parseable constrained output, and grade with a different model than the one that generated the output when possible.
Source: https://platform.claude.com/docs/en/test-and-evaluate/develop-tests (first 100k chars only). Confidence: medium-high.

G31. Topic 9. Anchoring rubric levels with per-level descriptors, and putting the full rubric text in the stable prefix, is consistent with the above but no primary source states "put the rubric in the stable prefix"; that is my inference from G21. Calibration against instructor-graded samples is the primary-source-backed check (G29). Unverified: specific claims about score drift, leniency or harshness bias for GPT-6 Luna.
Confidence: unverified (inference).

G32. Cross-cutting. Review prompts for contradictions after every edit (GPT-5 guide) and avoid duplicating the same rule in prompt and schema with different wording, since conflicting statements cost reasoning effort. Direct support: GPT-5 guide for contradictions; the duplication-wording point is an inference.
Source: GPT-5 cookbook guide. Confidence: medium.

## Candidate design implications (inference, not from sources)
1. Stable developer message: role, task, rubric-use rules, untrusted-data policy, scoring rules; no per-call values; identical across calls.
2. User message: per-call data only; submission inside a clearly labelled block (JSON string or XML with escaped closers); end with a one-line restatement of the task.
3. Schema carries format; descriptions carry field semantics; keep prose to what the schema cannot say.
4. Reasoning/evidence fields before score fields; cap lengths in descriptions.
5. Constant `session_id`/`prompt_cache_key` per batch; verify `cached_tokens` > 0 on call 2 with a real run.
6. Set max_output_tokens generously (>=25k per OpenAI advice) and handle incomplete/refusal.
7. Run injection fixtures (already in the eval plan) after any prompt edit.
