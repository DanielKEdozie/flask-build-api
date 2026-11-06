"""flask-build-api: Declarative CRUD REST API builder for Flask."""

import importlib

from marshmallow import fields
from .auto_schema import SchemaMixin, create_schema
from .conditional import generate_etag, match_etag
from .context import ApiContext
from .core import Api
from .decorators import action, route
from .docs import generate_openapi_spec, mount_swagger_docs
from .errors import (
    ApiError,
    ForbiddenError,
    NotFoundError,
    PreconditionFailedError,
    RateLimitExceededError,
    UnauthorizedError,
    ValidationErrorResponse,
    format_error_response,
    register_error_handlers,
)
from .idempotency import (
    IdempotencyStore,
    MemoryIdempotencyStore,
    RedisIdempotencyStore,
)
from .mixin import ApiMixin
from .plugins import (
    PluginProxy,
    PluginRegistry,
    auth,
    catalog,
    get_plugin,
    payment,
    storage,
    tasks,
)
from .schema import SchemaResolver
from .utils import ACTION_ALIASES, CANONICAL_ACTIONS, cast_identifier, normalize_action

__version__ = "0.1.0"

# Authentication is optional. These names are imported on first access so that
# ``import flask_build_api`` does not load token/JWT/HTTPAuth code unless the
# caller actually uses the auth suite.
_LAZY_ATTRS = {
    "ApiAuth": (".api_auth", "ApiAuth"),
    "TokenAuthManager": (".token_auth", "TokenAuthManager"),
    "TokenUserMixin": (".security", "TokenUserMixin"),
    "UserModelMixin": (".security", "UserModelMixin"),
    "admin_required": (".security", "admin_required"),
    "current_user": (".security", "current_user"),
    "login_required": (".security", "login_required"),
    "token_required": (".security", "token_required"),
    "security": (".security", None),
    "token": (".token", None),
}


def __getattr__(name: str):
    try:
        module_name, attr = _LAZY_ATTRS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None

    module = importlib.import_module(module_name, __name__)
    value = module if attr is None else getattr(module, attr)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(_LAZY_ATTRS))

__all__ = [
    "Api",
    "ApiAuth",
    "ApiContext",
    "ApiError",
    "ApiMixin",
    "ForbiddenError",
    "IdempotencyStore",
    "MemoryIdempotencyStore",
    "NotFoundError",
    "PreconditionFailedError",
    "RateLimitExceededError",
    "RedisIdempotencyStore",
    "SchemaMixin",
    "SchemaResolver",
    "TokenAuthManager",
    "TokenUserMixin",
    "UserModelMixin",
    "UnauthorizedError",
    "ValidationErrorResponse",
    "action",
    "admin_required",
    "auth",
    "catalog",
    "current_user",
    "login_required",
    "payment",
    "route",
    "security",
    "storage",
    "tasks",
    "token",
    "token_required",
    "get_plugin",
    "PluginProxy",
    "PluginRegistry",
    "cast_identifier",
    "create_schema",
    "fields",
    "format_error_response",
    "generate_etag",
    "generate_openapi_spec",
    "match_etag",
    "mount_swagger_docs",
    "normalize_action",
    "register_error_handlers",
    "ACTION_ALIASES",
    "CANONICAL_ACTIONS",
    "__version__",
]

