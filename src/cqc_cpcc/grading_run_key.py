#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Generate stable, deterministic grading run keys for caching.

This module provides utilities to generate deterministic hash keys based on
grading inputs. These keys are used to cache grading results in Streamlit
session state, preventing expensive re-grading on passive UI interactions.

Usage:
    >>> from cqc_cpcc.grading_run_key import generate_grading_run_key
    >>> 
    >>> run_key = generate_grading_run_key(
    ...     course_id="CSC151",
    ...     assignment_id="Exam1",
    ...     rubric_id="default_rubric",
    ...     rubric_version=1,
    ...     error_definition_ids=["MISSING_HEADER", "LOGIC_ERROR"],
    ...     file_metadata=[("student1.java", 1234)],
    ...     model_name="gpt-5-mini",
    ...     temperature=0.2
    ... )
    >>> print(run_key)  # Stable SHA256 hash
"""

import hashlib
import json
from typing import Optional


def generate_grading_run_key(
    course_id: str,
    assignment_id: str,
    rubric_id: str,
    rubric_version: int,
    error_definition_ids: Optional[list[str]] = None,
    file_metadata: Optional[list[tuple[str, int]]] = None,
    model_name: str = "gpt-5-mini",
    temperature: float = 0.2,
    debug_mode: bool = False,
    grading_mode: str = "rubric_and_errors",
) -> str:
    """Generate a deterministic hash key from grading inputs.
    
    The key is used to cache grading results in session state. Same inputs
    produce the same key, enabling result reuse across Streamlit reruns.
    
    Args:
        course_id: Course identifier (e.g., "CSC151")
        assignment_id: Assignment identifier (e.g., "Exam1")
        rubric_id: Rubric identifier
        rubric_version: Rubric version number
        error_definition_ids: List of enabled error definition IDs (sorted)
        file_metadata: List of (filename, file_size) tuples for uploaded files
        model_name: OpenAI model name
        temperature: Sampling temperature
        debug_mode: Whether debug mode is enabled
        grading_mode: Grading mode identifier
        
    Returns:
        SHA256 hash string (64 hex characters)
        
    Example:
        >>> key1 = generate_grading_run_key("CSC151", "Exam1", "rubric_v1", 1)
        >>> key2 = generate_grading_run_key("CSC151", "Exam1", "rubric_v1", 1)
        >>> assert key1 == key2  # Same inputs = same key
    """
    # Build normalized input dictionary
    inputs = {
        "course_id": course_id,
        "assignment_id": assignment_id,
        "rubric_id": rubric_id,
        "rubric_version": rubric_version,
        "error_definition_ids": sorted(error_definition_ids or []),
        "file_metadata": sorted(file_metadata or []),
        "model_name": model_name,
        "temperature": temperature,
        "debug_mode": debug_mode,
        "grading_mode": grading_mode,
    }
    
    # Serialize to JSON with sorted keys for determinism
    json_str = json.dumps(inputs, sort_keys=True, separators=(",", ":"))
    
    # Hash with SHA256
    hash_obj = hashlib.sha256(json_str.encode("utf-8"))
    return hash_obj.hexdigest()


def generate_file_metadata(file_paths: list[tuple[str, str]]) -> list[tuple[str, int]]:
    """Extract metadata from uploaded files for run key generation.
    
    Args:
        file_paths: List of (original_path, temp_path) tuples from Streamlit uploader
        
    Returns:
        List of (filename, file_size) tuples
        
    Example:
        >>> file_paths = [("student1.java", "/tmp/abc123")]
        >>> metadata = generate_file_metadata(file_paths)
        >>> print(metadata)  # [("student1.java", 1234)]
    """
    import os
    
    metadata = []
    for original_path, temp_path in file_paths:
        filename = os.path.basename(original_path)
        try:
            file_size = os.path.getsize(temp_path)
        except (OSError, IOError):
            # If temp file is not accessible, use 0
            file_size = 0
        
        metadata.append((filename, file_size))
    
    return metadata
