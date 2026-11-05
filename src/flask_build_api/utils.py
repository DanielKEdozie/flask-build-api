import uuid
import warnings
from typing import Any, Tuple


def resolve_deprecated_alias(
    new_name: str,
    new_value: Any,
    legacy_name: str,
    legacy_value: Any,
) -> Any:
    """Return the new argument value, falling back to a deprecated alias.

    Emits a ``DeprecationWarning`` when the legacy name is used. When both
    are supplied, the new name wins.
    """
    if legacy_value is not None:
        warnings.warn(
            f"'{legacy_name}' is deprecated; use '{new_name}' instead.",
            DeprecationWarning,
            stacklevel=3,
        )
        if new_value is None:
            return legacy_value
    return new_value


ACTION_ALIASES = {
    # list
    "list": "list",
    "get_list": "list",
    "all": "list",
    # retrieve
    "retrieve": "retrieve",
    "get": "retrieve",
    "detail": "retrieve",
    # create
    "create": "create",
    "post": "create",
    # update
    "update": "update",
    "put": "update",
    # partial_update
    "partial_update": "partial_update",
    "patch": "partial_update",
    # delete
    "delete": "delete",
    "destroy": "delete",
}

CANONICAL_ACTIONS = ("list", "retrieve", "create", "update", "partial_update", "delete")


def normalize_action(action: str) -> str:
    """Normalize any action alias (e.g. 'get', 'post', 'patch', 'destroy') to its canonical action."""
    if not action:
        return ""
    clean = str(action).strip().lower()
    return ACTION_ALIASES.get(clean, clean)


def cast_identifier(value: Any, pk_type: str) -> Tuple[Any, bool]:
    """Attempt to cast a string identifier into the target pk_type.
    
    Returns:
        (casted_value, success_boolean)
    """
    if value is None:
        return None, False

    pk_type = (pk_type or "string").strip().lower()

    if pk_type in ("int", "integer"):
        try:
            return int(value), True
        except (ValueError, TypeError):
            return value, False

    if pk_type == "uuid":
        try:
            return uuid.UUID(str(value)), True
        except (ValueError, TypeError, AttributeError):
            return value, False

    # Default: string / str
    return str(value), True


def is_type_match(value: Any, pk_type: str) -> bool:
    """Check if a string identifier matches the expected pk_type without crashing."""
    _, success = cast_identifier(value, pk_type)
    return success

