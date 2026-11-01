"""flask-build-api: Declarative CRUD REST API builder for Flask."""

from marshmallow import fields
from .auth import ApiAuth
from .auto_schema import SchemaMixin, create_schema
from .conditional import generate_etag, match_etag
from .context import ApiContext
from .core import Api
from .decorators import action, route
from .errors import (
    ApiError,
    ForbiddenError,
    NotFoundError,
    PreconditionFailedError,
    RateLimitExceededError,
    UnauthorizedError,
    ValidationErrorResponse,
    format_error_response,
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
from .token_auth import TokenAuthManager
from .utils import ACTION_ALIASES, CANONICAL_ACTIONS, cast_identifier, normalize_action

__version__ = "0.1.0"

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
    "UnauthorizedError",
    "ValidationErrorResponse",
    "action",
    "auth",
    "catalog",
    "payment",
    "route",
    "storage",
    "tasks",
    "get_plugin",
    "PluginProxy",
    "PluginRegistry",
    "cast_identifier",
    "create_schema",
    "fields",
    "format_error_response",
    "generate_etag",
    "match_etag",
    "normalize_action",
    "ACTION_ALIASES",
    "CANONICAL_ACTIONS",
    "__version__",
]

