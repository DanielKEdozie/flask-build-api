from typing import Any, Dict, Optional, Tuple
from flask import jsonify


class ApiError(Exception):
    """Base API exception that renders to a standardized JSON error response."""

    def __init__(
        self,
        message: str,
        status_code: int = 400,
        code: Optional[str] = None,
        details: Optional[Any] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code or "api_error"
        self.details = details
        self.headers = headers or {}


class NotFoundError(ApiError):
    def __init__(self, message: str = "Resource not found", details: Optional[Any] = None):
        super().__init__(message=message, status_code=404, code="not_found", details=details)


class ValidationErrorResponse(ApiError):
    def __init__(self, details: Any, message: str = "Validation error"):
        super().__init__(message=message, status_code=422, code="validation_error", details=details)
        self.errors = details


class ForbiddenError(ApiError):
    def __init__(self, message: str = "Permission denied", details: Optional[Any] = None):
        super().__init__(message=message, status_code=403, code="forbidden", details=details)


class UnauthorizedError(ApiError):
    def __init__(self, message: str = "Authentication required", details: Optional[Any] = None):
        super().__init__(message=message, status_code=401, code="unauthorized", details=details)


class PreconditionFailedError(ApiError):
    def __init__(self, message: str = "Resource has been modified (ETag mismatch)", details: Optional[Any] = None):
        super().__init__(message=message, status_code=412, code="precondition_failed", details=details)


class RateLimitExceededError(ApiError):
    def __init__(self, message: str = "Too many requests. Please slow down.", retry_after: int = 60):
        super().__init__(
            message=message,
            status_code=429,
            code="rate_limit_exceeded",
            headers={"Retry-After": str(retry_after)},
        )


def format_error_response(
    message: str,
    code: str = "error",
    details: Optional[Any] = None,
    status_code: int = 400,
    headers: Optional[Dict[str, str]] = None,
    request_id: Optional[str] = None,
) -> Tuple[Any, int, Dict[str, str]]:
    """Build standardized JSON error tuple for Flask."""
    payload: Dict[str, Any] = {
        "success": False,
        "error": {
            "code": code,
            "message": message,
        },
    }
    if details is not None:
        payload["error"]["details"] = details
        payload["errors"] = details
    if request_id:
        payload["error"]["request_id"] = request_id

    resp_headers = dict(headers or {})
    if request_id:
        resp_headers["X-Request-ID"] = request_id

    return jsonify(payload), status_code, resp_headers
