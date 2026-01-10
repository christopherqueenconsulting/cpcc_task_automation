#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Custom exceptions for OpenAI client wrapper.

This module defines clear exception types for different failure modes
when interacting with the OpenAI API.
"""


class OpenAITransportError(Exception):
    """Exception raised for transport-level errors.
    
    Raised for network, timeouts, 5xx, rate limits.
    These errors are typically transient and may be retried.
    
    Attributes:
        message: Human-readable error description
        status_code: HTTP status code if available
        retry_after: Seconds to wait before retry (for rate limits)
        correlation_id: Optional correlation ID for debug tracking
    """
    
    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        retry_after: int | None = None,
        correlation_id: str | None = None
    ):
        self.message = message
        self.status_code = status_code
        self.retry_after = retry_after
        self.correlation_id = correlation_id
        super().__init__(self.message)
    
    def __str__(self) -> str:
        parts = [self.message]
        if self.status_code:
            parts.append(f"(status: {self.status_code})")
        if self.retry_after:
            parts.append(f"(retry after: {self.retry_after}s)")
        if self.correlation_id:
            parts.append(f"(correlation_id: {self.correlation_id})")
        return " ".join(parts)


class OpenAISchemaValidationError(Exception):
    """Exception raised when LLM output fails Pydantic schema validation.
    
    This indicates the LLM returned JSON that doesn't match the expected structure.
    By default, these errors are not retried (but can be with repair flag).
    
    Attributes:
        message: Human-readable error description
        schema_name: Name of the Pydantic model that failed validation
        validation_errors: List of validation error details from Pydantic
        raw_output: The raw JSON string that failed validation
        correlation_id: Optional correlation ID for debug tracking
        decision_notes: Optional notes about why parsing failed
    """
    
    def __init__(
        self, 
        message: str, 
        schema_name: str | None = None, 
        validation_errors: list | None = None,
        raw_output: str | None = None,
        correlation_id: str | None = None,
        decision_notes: str | None = None
    ):
        self.message = message
        self.schema_name = schema_name
        self.validation_errors = validation_errors or []
        self.raw_output = raw_output
        self.correlation_id = correlation_id
        self.decision_notes = decision_notes
        super().__init__(self.message)
    
    def __str__(self) -> str:
        parts = [self.message]
        if self.schema_name:
            parts.append(f"(schema: {self.schema_name})")
        if self.validation_errors:
            parts.append(f"({len(self.validation_errors)} validation errors)")
        if self.correlation_id:
            parts.append(f"(correlation_id: {self.correlation_id})")
        if self.decision_notes:
            parts.append(f"(notes: {self.decision_notes})")
        return " ".join(parts)
