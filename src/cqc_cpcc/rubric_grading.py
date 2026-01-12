#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Rubric-based grading using OpenAI structured outputs.

This module implements rubric-based exam grading using the OpenAI wrapper.
It builds on the existing exam grading infrastructure but returns structured
rubric assessment results instead of just error definitions.

Key Features:
- Single-shot structured output (no RAG/vector DB)
- Deterministic prompt building
- Rubric breakdown with per-criterion scoring
- Optional error detection using error definitions
- Validates output against RubricAssessmentResult schema

Usage:
    >>> from cqc_cpcc.rubric_config import get_rubric_by_id
    >>> from cqc_cpcc.rubric_grading import grade_with_rubric
    >>> 
    >>> rubric = get_rubric_by_id("default_100pt_rubric")
    >>> result = await grade_with_rubric(
    ...     rubric=rubric,
    ...     assignment_instructions="Write a Hello World program...",
    ...     student_submission="public class Hello { ... }",
    ...     error_definitions=errors  # Optional
    ... )
    >>> print(result.total_points_earned)
    >>> print(result.overall_feedback)
"""

from typing import Optional
from langchain_core.callbacks import BaseCallbackHandler

from cqc_cpcc.rubric_models import Rubric, RubricAssessmentResult, DetectedError
from cqc_cpcc.error_definitions_models import ErrorDefinition
from cqc_cpcc.error_scoring import compute_error_based_score, aggregate_error_counts, get_error_count_for_severity
from cqc_cpcc.utilities.AI.openai_client import get_structured_completion
from cqc_cpcc.utilities.logger import logger


# Default model configuration
DEFAULT_GRADING_MODEL = "gpt-5-mini"
DEFAULT_TEMPERATURE = 0.2
DEFAULT_MAX_TOKENS = 4096


def build_rubric_grading_prompt(
    rubric: Rubric,
    assignment_instructions: str,
    student_submission: str,
    reference_solution: Optional[str] = None,
    error_definitions: Optional[list[ErrorDefinition]] = None,
) -> str:
    """Build a deterministic prompt for rubric-based grading.
    
    Args:
        rubric: The rubric to use for grading
        assignment_instructions: Assignment requirements and instructions
        student_submission: Student's code or work to grade
        reference_solution: Optional reference solution for comparison
        error_definitions: Optional list of ErrorDefinition objects to check against
        
    Returns:
        Formatted prompt string for OpenAI
    """
    prompt_parts = []
    
    # Header
    prompt_parts.append("# Grading Task")
    prompt_parts.append("You are grading a student submission using a structured rubric.")
    prompt_parts.append("")
    
    # Assignment instructions
    prompt_parts.append("## Assignment Instructions")
    prompt_parts.append(assignment_instructions)
    prompt_parts.append("")
    
    # Reference solution if provided
    if reference_solution:
        prompt_parts.append("## Reference Solution")
        prompt_parts.append(reference_solution)
        prompt_parts.append("")
    
    # Rubric details
    prompt_parts.append(f"## Grading Rubric: {rubric.title}")
    if rubric.description:
        prompt_parts.append(rubric.description)
    prompt_parts.append(f"**Total Points Possible:** {rubric.total_points_possible}")
    prompt_parts.append("")
    
    # Criteria breakdown
    prompt_parts.append("### Criteria")
    for criterion in rubric.criteria:
        if not criterion.enabled:
            continue  # Skip disabled criteria
        
        prompt_parts.append(f"\n**{criterion.name}** (Max: {criterion.max_points} points)")
        if criterion.description:
            prompt_parts.append(f"- {criterion.description}")
        
        if criterion.levels:
            prompt_parts.append("Performance Levels:")
            for level in sorted(criterion.levels, key=lambda l: l.score_max, reverse=True):
                prompt_parts.append(
                    f"  - **{level.label}** ({level.score_min}-{level.score_max} points): {level.description}"
                )
    
    prompt_parts.append("")
    
    # Overall bands if present
    if rubric.overall_bands:
        prompt_parts.append("### Overall Performance Bands")
        for band in sorted(rubric.overall_bands, key=lambda b: b.score_max, reverse=True):
            prompt_parts.append(f"- **{band.label}**: {band.score_min}-{band.score_max} points")
        prompt_parts.append("")
    
    # Error definitions if provided
    if error_definitions:
        # Filter to enabled errors if the objects have an 'enabled' attribute
        # Handle both ErrorDefinition (with enabled) and DetectedError (without) types
        enabled_errors = []
        for e in error_definitions:
            if hasattr(e, 'enabled'):
                if e.enabled:
                    enabled_errors.append(e)
            else:
                # No enabled field, include all
                enabled_errors.append(e)
        
        if enabled_errors:
            prompt_parts.append("### Error Definitions to Check")
            prompt_parts.append("Detect the following errors in the student submission and provide:")
            prompt_parts.append("1. A list of detected_errors with error code, severity, and occurrence count")
            prompt_parts.append("2. Aggregated counts by severity in error_counts_by_severity")
            prompt_parts.append("")
            
            # Group by severity - handle both severity_category and severity fields
            def get_severity(error):
                return getattr(error, 'severity_category', getattr(error, 'severity', 'unknown')).lower()
            
            major_errors = [e for e in enabled_errors if get_severity(e) == "major"]
            minor_errors = [e for e in enabled_errors if get_severity(e) == "minor"]
            other_errors = [e for e in enabled_errors if get_severity(e) not in ["major", "minor"]]
            
            if major_errors:
                prompt_parts.append("**Major Errors:**")
                for error in major_errors:
                    # Handle both error_id and code fields
                    error_code = getattr(error, 'error_id', getattr(error, 'code', 'UNKNOWN'))
                    error_desc = error.description
                    prompt_parts.append(f"- **{error_code}**: {error_desc}")
                    if hasattr(error, 'examples') and error.examples:
                        prompt_parts.append(f"  Examples: {'; '.join(error.examples)}")
            
            if minor_errors:
                prompt_parts.append("\n**Minor Errors:**")
                for error in minor_errors:
                    error_code = getattr(error, 'error_id', getattr(error, 'code', 'UNKNOWN'))
                    error_desc = error.description
                    prompt_parts.append(f"- **{error_code}**: {error_desc}")
                    if hasattr(error, 'examples') and error.examples:
                        prompt_parts.append(f"  Examples: {'; '.join(error.examples)}")
            
            if other_errors:
                prompt_parts.append("\n**Other Errors:**")
                for error in other_errors:
                    prompt_parts.append(f"- **{error.error_id}** ({error.severity_category}): {error.description}")
                    if error.examples:
                        prompt_parts.append(f"  Examples: {'; '.join(error.examples)}")
            
            prompt_parts.append("")
    
    # Student submission
    prompt_parts.append("## Student Submission to Grade")
    prompt_parts.append("```")
    prompt_parts.append(student_submission)
    prompt_parts.append("```")
    prompt_parts.append("")
    
    # Grading instructions
    prompt_parts.append("## Grading Instructions")
    prompt_parts.append("1. Evaluate the student submission against each criterion in the rubric")
    prompt_parts.append("2. For criteria with scoring_mode='manual':")
    prompt_parts.append("   - Assign points earned for each criterion (0 to max_points)")
    prompt_parts.append("   - Select the most appropriate performance level label if levels are defined")
    prompt_parts.append("3. For criteria with scoring_mode='level_band':")
    prompt_parts.append("   - SELECT the most appropriate performance level label from the defined levels")
    prompt_parts.append("   - DO NOT assign points (backend will compute from level range)")
    prompt_parts.append("   - Set points_earned to 0 as a placeholder")
    prompt_parts.append("   - Provide feedback explaining why this level was selected")
    prompt_parts.append("4. For criteria with scoring_mode='error_count':")
    prompt_parts.append("   - DO NOT assign points (backend will compute based on error counts)")
    prompt_parts.append("   - Set points_earned to 0 as a placeholder")
    prompt_parts.append("   - Still provide feedback describing detected errors")
    prompt_parts.append("5. Provide specific, actionable feedback for each criterion")
    prompt_parts.append("6. Include evidence from the submission to support your assessment")
    prompt_parts.append("7. DO NOT calculate total points - backend will compute from individual criteria")
    prompt_parts.append("8. Set total_points_earned to 0 as placeholder (backend will recalculate)")
    prompt_parts.append("9. Provide overall summary feedback")
    
    if error_definitions:
        prompt_parts.append("10. Check for defined errors and include them in detected_errors field")
        prompt_parts.append("   - For each detected error, set 'code' to the error_id (e.g., 'CSC_151_EXAM_1_SYNTAX_ERROR')")
        prompt_parts.append("   - Set 'severity' to the severity_category (e.g., 'major', 'minor')")
        prompt_parts.append("   - Set 'occurrences' to the count of times this error appears")
        prompt_parts.append("11. Populate error_counts_by_severity with aggregated counts (e.g., {'major': 2, 'minor': 5})")
        prompt_parts.append("12. Optionally populate error_counts_by_id with per-error counts")
    
    prompt_parts.append("")
    prompt_parts.append("Return your assessment as structured JSON matching the RubricAssessmentResult schema.")
    prompt_parts.append("Be objective, fair, and constructive in your feedback.")
    prompt_parts.append("")
    prompt_parts.append("IMPORTANT: The backend will compute all point totals and percentages deterministically.")
    prompt_parts.append("Your role is to identify performance levels, detect errors, and provide feedback.")
    
    return "\n".join(prompt_parts)


async def grade_with_rubric(
    rubric: Rubric,
    assignment_instructions: str,
    student_submission: str,
    reference_solution: Optional[str] = None,
    error_definitions: Optional[list[ErrorDefinition]] = None,
    model_name: str = DEFAULT_GRADING_MODEL,
    temperature: float = DEFAULT_TEMPERATURE,
    callback: Optional[BaseCallbackHandler] = None,
) -> RubricAssessmentResult:
    """Grade a student submission using a rubric.
    
    This function uses OpenAI structured outputs to grade a submission according to
    a defined rubric. It returns a complete RubricAssessmentResult with per-criterion
    scores, feedback, and overall assessment.
    
    If error_definitions are provided and the rubric has error_count criteria, this
    function will:
    1. Ask OpenAI to detect errors and return counts
    2. Use backend deterministic scoring for error_count criteria
    3. Recalculate total_points_earned including error-based scores
    
    Args:
        rubric: The grading rubric to use
        assignment_instructions: Assignment requirements
        student_submission: Student's code or work to grade
        reference_solution: Optional reference solution for comparison
        error_definitions: Optional list of ErrorDefinition objects to check
        model_name: OpenAI model to use (default: gpt-5-mini)
        temperature: Sampling temperature (default: 0.2)
        callback: Optional LangChain callback for compatibility
        
    Returns:
        RubricAssessmentResult with complete grading breakdown
        
    Raises:
        ValueError: If rubric validation fails or LLM output is invalid
        
    Example:
        >>> from cqc_cpcc.rubric_config import get_rubric_by_id
        >>> from cqc_cpcc.error_definitions_config import get_error_definitions
        >>> rubric = get_rubric_by_id("default_100pt_rubric")
        >>> errors = get_error_definitions("CSC151", "Exam1")
        >>> result = await grade_with_rubric(
        ...     rubric=rubric,
        ...     assignment_instructions="Write Hello World",
        ...     student_submission="print('Hello World')",
        ...     error_definitions=errors
        ... )
        >>> print(f"Score: {result.total_points_earned}/{result.total_points_possible}")
    """
    # Build the prompt
    prompt = build_rubric_grading_prompt(
        rubric=rubric,
        assignment_instructions=assignment_instructions,
        student_submission=student_submission,
        reference_solution=reference_solution,
        error_definitions=error_definitions,
    )
    
    logger.info(
        f"Grading with rubric '{rubric.rubric_id}': "
        f"{len([c for c in rubric.criteria if c.enabled])} enabled criteria, "
        f"{rubric.total_points_possible} total points"
    )
    
    try:
        # Call OpenAI with structured output validation
        result = await get_structured_completion(
            prompt=prompt,
            model_name=model_name,
            schema_model=RubricAssessmentResult,
            temperature=temperature,
            max_tokens=DEFAULT_MAX_TOKENS,
        )
        
        # Log raw OpenAI response for debugging
        logger.info(
            f"OpenAI raw response: total_points_earned={result.total_points_earned}, "
            f"rubric_id='{result.rubric_id}', rubric_version='{result.rubric_version}'"
        )
        logger.debug(f"OpenAI criteria count: {len(result.criteria_results)}")
        for i, cr in enumerate(result.criteria_results):
            logger.debug(
                f"  Criterion {i+1}/{len(result.criteria_results)}: {cr.criterion_id} = "
                f"{cr.points_earned}/{cr.points_possible} (level: {cr.selected_level_label or 'N/A'})"
            )
        
        # Post-process: Apply backend scoring for non-manual criteria
        result = apply_backend_scoring(rubric, result)
        
        # Validate that result matches rubric
        if result.rubric_id != rubric.rubric_id:
            logger.warning(
                f"Result rubric_id '{result.rubric_id}' does not match input rubric_id '{rubric.rubric_id}'"
            )
        
        if result.total_points_possible != rubric.total_points_possible:
            logger.warning(
                f"Result total_points_possible ({result.total_points_possible}) "
                f"does not match rubric ({rubric.total_points_possible})"
            )
        
        logger.info(
            f"Grading complete: {result.total_points_earned}/{result.total_points_possible} points "
            f"({len(result.criteria_results)} criteria assessed)"
        )
        
        if result.detected_errors:
            logger.info(f"Detected {len(result.detected_errors)} errors")
        
        return result
        
    except Exception as e:
        logger.error(f"Rubric grading failed: {e}")
        raise ValueError(f"Failed to grade with rubric: {e}")


def apply_backend_scoring(rubric: Rubric, result: RubricAssessmentResult) -> RubricAssessmentResult:
    """Apply backend deterministic scoring for non-manual criteria.
    
    This function is the main entry point for backend scoring computation. It:
    1. Handles error_count criteria: applies error normalization and computes scores
    2. Handles level_band criteria: computes points from selected performance levels
    3. Handles CSC151 v2.0 program_performance criterion (special case)
    4. Recalculates total_points_earned
    5. Recalculates overall_band_label using deterministic logic
    6. Stores original and effective error counts for transparency
    
    Args:
        rubric: The rubric used for grading
        result: The initial result from OpenAI (may have placeholder scores)
        
    Returns:
        Updated RubricAssessmentResult with backend-computed scores
    """
    from cqc_cpcc.error_scoring import normalize_errors, select_program_performance_level, get_error_count_for_severity
    from cqc_cpcc.scoring import score_level_band_criterion, aggregate_rubric_result
    
    logger.info(f"=== Backend Scoring Start ===")
    logger.info(f"Input result: total_points_earned={result.total_points_earned}, total_points_possible={result.total_points_possible}")
    logger.info(f"Input result has {len(result.criteria_results)} criterion results")
    logger.info(f"Input result error_counts_by_severity: {result.error_counts_by_severity}")
    
    # Identify criteria that need backend scoring
    level_band_criteria = [c for c in rubric.criteria if c.enabled and c.scoring_mode == "level_band"]
    error_count_criteria = [c for c in rubric.criteria if c.enabled and c.scoring_mode == "error_count"]
    
    # Check if this is CSC151 v2.0 rubric (has program_performance criterion)
    has_program_performance = any(
        c.criterion_id == "program_performance" for c in rubric.criteria if c.enabled
    )
    
    logger.info(f"Criteria analysis: level_band={len(level_band_criteria)}, error_count={len(error_count_criteria)}, has_program_performance={has_program_performance}")
    
    # If no backend scoring needed, return as-is
    if not level_band_criteria and not error_count_criteria and not has_program_performance:
        logger.info("No backend scoring needed (all criteria are manual mode)")
        return result
    
    logger.info(
        f"Applying backend scoring: {len(level_band_criteria)} level_band, "
        f"{len(error_count_criteria)} error_count criteria"
    )
    
    # Extract error counts if needed
    original_major = 0
    original_minor = 0
    effective_major = 0
    effective_minor = 0
    
    if error_count_criteria or has_program_performance:
        has_error_counts = result.error_counts_by_severity is not None
        
        if not has_error_counts:
            logger.warning(
                f"Rubric has error-based criteria but no error_counts_by_severity in result."
            )
            
            # Try to compute error counts from detected_errors if present
            if result.detected_errors:
                logger.info(f"Computing error_counts_by_severity from {len(result.detected_errors)} detected_errors")
                error_counts = {}
                for error in result.detected_errors:
                    severity = error.severity.lower() if error.severity else "unknown"
                    error_counts[severity] = error_counts.get(severity, 0) + (error.occurrences or 1)
                
                logger.info(f"Computed error_counts_by_severity: {error_counts}")
                # Use the computed counts
                original_major = error_counts.get("major", 0)
                original_minor = error_counts.get("minor", 0)
            else:
                logger.warning("No detected_errors either. Using 0 errors for scoring.")
                # Default to 0 errors
                original_major = 0
                original_minor = 0
        else:
            # Extract error counts by severity
            original_major = get_error_count_for_severity(result.error_counts_by_severity, "major")
            original_minor = get_error_count_for_severity(result.error_counts_by_severity, "minor")
        
        logger.info(
            f"Original error counts: {original_major} major, {original_minor} minor"
        )
        
        # Apply error normalization (4 minor = 1 major for CSC151)
        effective_major, effective_minor = normalize_errors(original_major, original_minor)
        
        logger.info(
            f"Effective error counts after normalization: {effective_major} major, {effective_minor} minor"
        )
    
    # Update criterion results with computed scores
    updated_criteria_results = []
    for criterion_result in result.criteria_results:
        logger.info(f"Processing criterion '{criterion_result.criterion_id}': current points_earned={criterion_result.points_earned}, selected_level_label='{criterion_result.selected_level_label}'")
        
        # Find corresponding rubric criterion
        rubric_criterion = next(
            (c for c in rubric.criteria if c.criterion_id == criterion_result.criterion_id),
            None
        )
        
        if not rubric_criterion:
            logger.warning(
                f"Criterion '{criterion_result.criterion_id}' in result not found in rubric"
            )
            updated_criteria_results.append(criterion_result)
            continue
        
        logger.info(f"Rubric criterion found: scoring_mode='{rubric_criterion.scoring_mode}', criterion_id='{rubric_criterion.criterion_id}'")
        
        # Handle different scoring modes
        if rubric_criterion.criterion_id == "program_performance":
            # Special handling for CSC151 v2.0 program_performance criterion
            logger.info(f"Using CSC151 program_performance scoring with effective_major={effective_major}, effective_minor={effective_minor}")
            level_label, score = select_program_performance_level(
                effective_major,
                effective_minor,
                assignment_submitted=True  # Assume submitted if we have a result
            )
            
            logger.info(f"CSC151 program_performance computed: level_label='{level_label}', score={score}")
            
            # Update the criterion result
            criterion_result.points_earned = score
            criterion_result.selected_level_label = level_label
            
            logger.info(
                f"CSC151 program_performance: {level_label} = {score}/100 points "
                f"(effective: {effective_major} major, {effective_minor} minor)"
            )
        
        elif rubric_criterion.scoring_mode == "level_band":
            # Level-band scoring: compute points from selected level
            if not criterion_result.selected_level_label:
                logger.error(
                    f"Criterion '{criterion_result.criterion_id}' has scoring_mode='level_band' "
                    f"but no selected_level_label in result. Cannot compute score."
                )
                # Keep whatever points the LLM assigned (may be 0)
            else:
                try:
                    scoring_result = score_level_band_criterion(
                        criterion_result.selected_level_label,
                        rubric_criterion,
                        points_strategy=rubric_criterion.points_strategy
                    )
                    
                    # Update the criterion result with computed points
                    criterion_result.points_earned = scoring_result["points_awarded"]
                    
                    logger.info(
                        f"Level-band scoring for '{criterion_result.criterion_id}': "
                        f"{criterion_result.selected_level_label} = {scoring_result['points_awarded']}/{criterion_result.points_possible} "
                        f"(range: {scoring_result['score_min']}-{scoring_result['score_max']}, strategy: {scoring_result['points_strategy']})"
                    )
                except ValueError as e:
                    logger.error(
                        f"Failed to score level_band criterion '{criterion_result.criterion_id}': {e}"
                    )
                    # Keep whatever points the LLM assigned (may be 0)
        
        elif rubric_criterion.scoring_mode == "error_count":
            # Error-count scoring: compute points from error counts
            from cqc_cpcc.scoring import score_error_count_criterion
            
            scoring_result = score_error_count_criterion(
                effective_major,  # Use effective counts
                effective_minor,
                rubric_criterion
            )
            
            # Update the criterion result
            criterion_result.points_earned = scoring_result["points_awarded"]
            
            logger.info(
                f"Error-count scoring for '{criterion_result.criterion_id}': "
                f"{scoring_result['points_awarded']}/{criterion_result.points_possible} "
                f"(effective: {scoring_result['effective_major']} major, {scoring_result['effective_minor']} minor)"
            )
        
        # scoring_mode='manual' - keep LLM-assigned points as-is
        
        updated_criteria_results.append(criterion_result)
    
    # Use scoring engine to aggregate results
    aggregation = aggregate_rubric_result(rubric, updated_criteria_results, recalculate_overall_band=True)
    
    logger.info(f"Aggregation result: total_points_earned={aggregation['total_points_earned']}, percentage={aggregation['percentage']}, overall_band={aggregation['overall_band_label']}")
    
    # Log detailed breakdown for debugging
    logger.debug("=== Criterion Scoring Breakdown ===")
    for cr in updated_criteria_results:
        logger.debug(
            f"  {cr.criterion_id}: {cr.points_earned}/{cr.points_possible} pts "
            f"(level: {cr.selected_level_label or 'N/A'})"
        )
    logger.debug(f"=== Total: {aggregation['total_points_earned']}/{aggregation['total_points_possible']} ===")
    
    # Create updated result with backend-computed scores
    updated_result = result.model_copy(update={
        "criteria_results": updated_criteria_results,
        "total_points_earned": aggregation["total_points_earned"],
        "overall_band_label": aggregation["overall_band_label"],
        "original_major_errors": original_major if (error_count_criteria or has_program_performance) else None,
        "original_minor_errors": original_minor if (error_count_criteria or has_program_performance) else None,
        "effective_major_errors": effective_major if (error_count_criteria or has_program_performance) else None,
        "effective_minor_errors": effective_minor if (error_count_criteria or has_program_performance) else None,
    })
    
    logger.info(
        f"Backend scoring complete: {updated_result.total_points_earned}/{updated_result.total_points_possible} "
        f"({aggregation['percentage']:.1f}%), band='{updated_result.overall_band_label}'"
    )
    logger.info(f"=== Backend Scoring End ===")
    
    return updated_result


# Keep old function name for backwards compatibility
def apply_error_based_scoring(rubric: Rubric, result: RubricAssessmentResult) -> RubricAssessmentResult:
    """Apply backend error-based scoring for criteria with scoring_mode='error_count'.
    
    DEPRECATED: Use apply_backend_scoring instead. This function is kept for backwards compatibility.
    
    This function:
    1. Applies Minor→Major error normalization (for CSC151 v2.0 rubric)
    2. Identifies criteria with scoring_mode='error_count'
    3. Computes their scores using error counts from result
    4. Updates the corresponding CriterionResult with computed points
    5. Recalculates total_points_earned
    6. Stores original and effective error counts for transparency
    
    Args:
        rubric: The rubric with potentially error_count criteria
        result: The initial result from OpenAI (may have placeholder scores for error_count criteria)
        
    Returns:
        Updated RubricAssessmentResult with backend-computed error-based scores
    """
    logger.warning("apply_error_based_scoring is deprecated, use apply_backend_scoring instead")
    return apply_backend_scoring(rubric, result)
    
    logger.info(
        f"Updated total score after error-based scoring: "
        f"{updated_result.total_points_earned}/{updated_result.total_points_possible} "
        f"(was {result.total_points_earned})"
    )
    
    return updated_result


class RubricGrader:
    """Reusable rubric grader with stored configuration.
    
    This class provides a convenient interface for grading multiple submissions
    with the same rubric and configuration.
    
    Attributes:
        rubric: The grading rubric
        assignment_instructions: Assignment requirements
        reference_solution: Optional reference solution
        error_definitions: Optional error definitions
        model_name: OpenAI model to use
        temperature: Sampling temperature
    """
    
    def __init__(
        self,
        rubric: Rubric,
        assignment_instructions: str,
        reference_solution: Optional[str] = None,
        error_definitions: Optional[list[ErrorDefinition]] = None,
        model_name: str = DEFAULT_GRADING_MODEL,
        temperature: float = DEFAULT_TEMPERATURE,
    ):
        """Initialize rubric grader with configuration.
        
        Args:
            rubric: The grading rubric
            assignment_instructions: Assignment requirements
            reference_solution: Optional reference solution
            error_definitions: Optional error definitions (list of ErrorDefinition objects)
            model_name: OpenAI model to use
            temperature: Sampling temperature
        """
        self.rubric = rubric
        self.assignment_instructions = assignment_instructions
        self.reference_solution = reference_solution
        self.error_definitions = error_definitions
        self.model_name = model_name
        self.temperature = temperature
    
    async def grade(
        self,
        student_submission: str,
        callback: Optional[BaseCallbackHandler] = None,
    ) -> RubricAssessmentResult:
        """Grade a student submission.
        
        Args:
            student_submission: Student's code or work to grade
            callback: Optional LangChain callback
            
        Returns:
            RubricAssessmentResult with complete grading breakdown
        """
        return await grade_with_rubric(
            rubric=self.rubric,
            assignment_instructions=self.assignment_instructions,
            student_submission=student_submission,
            reference_solution=self.reference_solution,
            error_definitions=self.error_definitions,
            model_name=self.model_name,
            temperature=self.temperature,
            callback=callback,
        )
