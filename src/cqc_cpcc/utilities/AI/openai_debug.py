#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""OpenAI request/response debug instrumentation.

This module provides debugging capabilities for OpenAI API calls, including:
- Correlation ID generation for request tracking
- Request/response payload capture
- Decision notes for empty response diagnosis
- Optional PII redaction
- File-based logging to JSON files

Usage:
    from cqc_cpcc.utilities.AI.openai_debug import (
        create_correlation_id,
        record_request,
        record_response,
        should_debug
    )
    
    if should_debug():
        corr_id = create_correlation_id()
        record_request(corr_id, model="gpt-5-mini", messages=[...])
        # ... make API call ...
        record_response(corr_id, response=response, decision_notes="success")
"""

import json
import logging
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from cqc_cpcc.utilities.env_constants import (
    CQC_OPENAI_DEBUG,
    CQC_OPENAI_DEBUG_REDACT,
    CQC_OPENAI_DEBUG_SAVE_DIR,
)

# Dedicated logger for OpenAI debug
debug_logger = logging.getLogger("openai.debug")


def should_debug() -> bool:
    """Check if OpenAI debug mode is enabled.
    
    Returns:
        True if debug mode is enabled, False otherwise
    """
    return CQC_OPENAI_DEBUG


def create_correlation_id() -> str:
    """Generate a short correlation ID for request tracking.
    
    Uses uuid4 and takes first 8 characters for readability.
    
    Returns:
        Short correlation ID string (e.g., "a1b2c3d4")
    """
    return str(uuid.uuid4())[:8]


def _redact_sensitive_data(data: Any) -> Any:
    """Recursively redact sensitive data from a data structure.
    
    Redacts:
    - API keys (any field with 'key', 'token', 'secret', 'password' in name)
    - Email addresses
    - Potential PII patterns (SSN, phone numbers)
    
    Args:
        data: Data structure to redact (dict, list, str, or primitive)
        
    Returns:
        Redacted copy of the data structure
    """
    if not CQC_OPENAI_DEBUG_REDACT:
        return data
    
    if isinstance(data, dict):
        redacted = {}
        for key, value in data.items():
            key_lower = key.lower()
            # Check for sensitive field names
            if any(sensitive in key_lower for sensitive in ['key', 'token', 'secret', 'password', 'api']):
                redacted[key] = "***REDACTED***"
            else:
                redacted[key] = _redact_sensitive_data(value)
        return redacted
    
    elif isinstance(data, list):
        return [_redact_sensitive_data(item) for item in data]
    
    elif isinstance(data, str):
        # Redact email addresses
        text = re.sub(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', '***EMAIL***', data)
        # Redact SSN patterns (XXX-XX-XXXX)
        text = re.sub(r'\b\d{3}-\d{2}-\d{4}\b', '***SSN***', text)
        # Redact phone numbers (various formats)
        text = re.sub(r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b', '***PHONE***', text)
        return text
    
    else:
        return data


def _save_to_file(correlation_id: str, data_type: str, data: dict) -> None:
    """Save debug data to JSON file.
    
    Args:
        correlation_id: Correlation ID for this request
        data_type: Type of data ('request', 'response', 'notes')
        data: Data dictionary to save
    """
    if not CQC_OPENAI_DEBUG_SAVE_DIR:
        return
    
    try:
        # Create save directory if it doesn't exist
        save_dir = Path(CQC_OPENAI_DEBUG_SAVE_DIR)
        save_dir.mkdir(parents=True, exist_ok=True)
        
        # Generate filename with timestamp and correlation ID
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filename = f"{timestamp}_{correlation_id}_{data_type}.json"
        filepath = save_dir / filename
        
        # Write JSON file
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, default=str)
        
        debug_logger.debug(f"Saved {data_type} data to {filepath}")
        
    except Exception as e:
        # Don't let file writing errors break the main flow
        debug_logger.warning(f"Failed to save {data_type} data to file: {e}")


def record_request(
    correlation_id: str,
    model: str,
    messages: list[dict],
    response_format: dict,
    schema_name: str,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    **other_params
) -> None:
    """Record an OpenAI API request for debugging.
    
    Args:
        correlation_id: Correlation ID for this request
        model: Model name (e.g., "gpt-5-mini")
        messages: List of message dicts (role, content)
        response_format: Response format dict with schema
        schema_name: Name of the Pydantic schema model
        temperature: Optional temperature parameter
        max_tokens: Optional max tokens parameter
        **other_params: Any other API parameters
    """
    if not should_debug():
        return
    
    try:
        # Build request data
        request_data = {
            "correlation_id": correlation_id,
            "timestamp": datetime.utcnow().isoformat(),
            "model": model,
            "schema_name": schema_name,
            "endpoint": "chat.completions.create (structured outputs)",
            "request": {
                "messages": messages,
                "response_format": {
                    "type": response_format.get("type"),
                    "json_schema": {
                        "name": response_format.get("json_schema", {}).get("name"),
                        "strict": response_format.get("json_schema", {}).get("strict"),
                        # Include schema structure summary but not full schema (too verbose)
                        "schema_summary": f"Normalized schema with {len(str(response_format.get('json_schema', {}).get('schema', {})))} chars"
                    }
                },
            },
        }
        
        # Add optional parameters
        if temperature is not None:
            request_data["request"]["temperature"] = temperature
        if max_tokens is not None:
            request_data["request"]["max_tokens"] = max_tokens
        for key, value in other_params.items():
            request_data["request"][key] = value
        
        # Redact sensitive data
        redacted_data = _redact_sensitive_data(request_data)
        
        # Log to console/file
        debug_logger.info(
            f"[{correlation_id}] OpenAI Request: model={model}, schema={schema_name}"
        )
        debug_logger.debug(f"[{correlation_id}] Full request: {json.dumps(redacted_data, indent=2, default=str)}")
        
        # Save to file if configured
        _save_to_file(correlation_id, "request", redacted_data)
        
    except Exception as e:
        debug_logger.warning(f"Failed to record request: {e}")


def record_response(
    correlation_id: str,
    response: Any,
    schema_name: str,
    decision_notes: str,
    output_text: Optional[str] = None,
    output_parsed: Optional[Any] = None,
    error: Optional[Exception] = None,
) -> None:
    """Record an OpenAI API response for debugging.
    
    Args:
        correlation_id: Correlation ID for this request
        response: Raw response object from OpenAI (or None if error)
        schema_name: Name of the Pydantic schema model
        decision_notes: Notes about why output_parsed is empty or what happened
        output_text: Extracted text content from response
        output_parsed: Parsed Pydantic model (or None if failed)
        error: Exception if one occurred
    """
    if not should_debug():
        return
    
    try:
        # Build response data
        response_data = {
            "correlation_id": correlation_id,
            "timestamp": datetime.utcnow().isoformat(),
            "schema_name": schema_name,
            "decision_notes": decision_notes,
        }
        
        # Add response metadata if available
        if response:
            response_data["response_metadata"] = {
                "id": getattr(response, 'id', None),
                "model": getattr(response, 'model', None),
                "created": getattr(response, 'created', None),
                "object": getattr(response, 'object', None),
            }
            
            # Add usage info if available
            if hasattr(response, 'usage') and response.usage:
                response_data["usage"] = {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                }
            
            # Check for refusal
            if hasattr(response, 'choices') and response.choices:
                choice = response.choices[0]
                if hasattr(choice, 'message'):
                    message = choice.message
                    if hasattr(message, 'refusal') and message.refusal:
                        response_data["refusal"] = message.refusal
        
        # Add output data
        response_data["output"] = {
            "text": output_text[:500] if output_text else None,  # Truncate to 500 chars
            "parsed_present": output_parsed is not None,
            "parsed_type": type(output_parsed).__name__ if output_parsed else None,
        }
        
        # Add error info if present
        if error:
            response_data["error"] = {
                "type": type(error).__name__,
                "message": str(error),
            }
        
        # Redact sensitive data
        redacted_data = _redact_sensitive_data(response_data)
        
        # Log to console/file
        debug_logger.info(
            f"[{correlation_id}] OpenAI Response: schema={schema_name}, "
            f"parsed={output_parsed is not None}, notes={decision_notes}"
        )
        debug_logger.debug(f"[{correlation_id}] Full response: {json.dumps(redacted_data, indent=2, default=str)}")
        
        # Save to file if configured
        _save_to_file(correlation_id, "response", redacted_data)
        
        # Save decision notes separately for easy access
        notes_data = {
            "correlation_id": correlation_id,
            "timestamp": datetime.utcnow().isoformat(),
            "schema_name": schema_name,
            "decision_notes": decision_notes,
            "parsed_success": output_parsed is not None,
        }
        _save_to_file(correlation_id, "notes", notes_data)
        
    except Exception as e:
        debug_logger.warning(f"Failed to record response: {e}")


def get_debug_context(correlation_id: str) -> dict:
    """Get debug context for a correlation ID (for UI display).
    
    Args:
        correlation_id: Correlation ID to look up
        
    Returns:
        Dictionary with request, response, and notes data (or empty dict if not found)
    """
    if not should_debug() or not CQC_OPENAI_DEBUG_SAVE_DIR:
        return {}
    
    try:
        save_dir = Path(CQC_OPENAI_DEBUG_SAVE_DIR)
        if not save_dir.exists():
            return {}
        
        # Find files matching this correlation ID
        request_files = list(save_dir.glob(f"*_{correlation_id}_request.json"))
        response_files = list(save_dir.glob(f"*_{correlation_id}_response.json"))
        notes_files = list(save_dir.glob(f"*_{correlation_id}_notes.json"))
        
        context = {
            "correlation_id": correlation_id,
        }
        
        # Load request data
        if request_files:
            with open(request_files[0], 'r', encoding='utf-8') as f:
                context["request"] = json.load(f)
        
        # Load response data
        if response_files:
            with open(response_files[0], 'r', encoding='utf-8') as f:
                context["response"] = json.load(f)
        
        # Load notes data
        if notes_files:
            with open(notes_files[0], 'r', encoding='utf-8') as f:
                context["notes"] = json.load(f)
        
        return context
        
    except Exception as e:
        debug_logger.warning(f"Failed to load debug context: {e}")
        return {}
