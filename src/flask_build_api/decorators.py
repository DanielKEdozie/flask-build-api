from functools import wraps
from typing import Any, Callable, List, Optional, Union


def action(
    methods: Optional[List[str]] = None,
    detail: bool = False,
    url_path: Optional[str] = None,
    schema: Optional[Any] = None,
    response_schema: Optional[Any] = None,
    validate_schema: bool = True,
    **kwargs
) -> Callable:
    """Decorator to mark a method on an ApiMixin as a custom endpoint.
    
    Args:
        methods: HTTP methods supported (default: ['GET']).
        detail: If True, route includes resource PK: /<pk>/<url_path>
                If False, route is a collection endpoint: /<url_path>
        url_path: Custom path suffix. Defaults to method name.
        schema: Marshmallow Schema class/instance, tuple (req_schema, res_schema),
                or dict {'req': ..., 'res': ...} for input validation & output serialization.
        response_schema: Optional Marshmallow Schema class/instance for output serialization.
        validate_schema: Whether to validate incoming request data using schema (default: True).
    """
    methods = [m.upper() for m in (methods or ["GET"])]

    def decorator(func: Callable) -> Callable:
        setattr(func, "_is_action", True)
        setattr(func, "_action_methods", methods)
        setattr(func, "_action_detail", detail)
        setattr(func, "_action_url_path", url_path or func.__name__)
        setattr(func, "_action_schema", schema)
        setattr(func, "_action_response_schema", response_schema)
        setattr(func, "_action_validate_schema", validate_schema)
        setattr(func, "_action_kwargs", kwargs)
        return func

    return decorator


# Synonym alias: @route can be used interchangeably with @action
route = action


