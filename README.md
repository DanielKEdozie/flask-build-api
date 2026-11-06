# flask-build-api

A declarative, high-productivity REST API builder for Flask, SQLAlchemy, and Marshmallow.

## Features

- **Dual Style**: Declarative `api.create_resource(...)` factory and class-based `class UserApi(ApiMixin):`.
- **Auto-SQLAlchemy Marshmallow (`SchemaMixin` & `create_schema`)**: Zero-boilerplate schema generation directly from SQLAlchemy models. Use declarative `class UserSchema(SchemaMixin):` or dynamic `api.create_schema(Model, ...)` with relationship traversal (`depth`), field filtering (`fields`, `exclude`, `load_only`, `dump_only`), writeable relationships (`write=True`), and custom field injection. If `schema` is omitted in `api.create_resource(model=Model)`, a schema is automatically generated on the fly!
- **Singleton Resources (`singleton = True`)**: First-class support for singletons where no ID exists in the URL path (e.g. `/api/settings` or nested 1-to-1 relations like `/api/users/<user_id>/profile`). Operates with automatic conflict prevention (409 Conflict if record exists on `POST`) and direct `GET`, `POST`, `PUT`, `PATCH`, `DELETE` at the base route.
- **Configurable Enveloping (`envelope_key` / `data_envelope_key`)**: Choose the top-level key wrapping response payloads (defaults to `"data"`, e.g. `{"success": true, "data": [...]}` or customize to `"result"`, `"payload"`, or `None` for flat responses).
- **Nested Sub-Resources**: First-class support for child resources via `api.create_nested_resource(...)` or `user_api.create_nested_resource(...)` with automatic query scoping and foreign key injection.
- **Dynamic Pagination Toggle**: Turn off pagination on demand via `?paginate=false` or `?all=true` (with configurable `allow_disable_pagination` and safety ceiling `max_unpaginated_limit`).
- **Sparse Fieldsets**: Request only specific fields using `?fields=id,name,price`, reducing network payload size and serialization overhead.
- **Idempotency Support**: Send `Idempotency-Key: <key>` on `POST` to prevent duplicate writes or double charges during network retries.
- **Optimistic Concurrency & ETags**: Automatic `ETag` generation; supports `If-None-Match` (304 Not Modified) and `If-Match` (preventing lost updates with 412 Precondition Failed).
- **Request Tracing**: Automatic `X-Request-ID` generation and correlation injected into response headers, `ctx.request_id`, and error bodies.
- **Atomic Bulk Operations**: High-performance batch insertions (`POST /resource/bulk`) and deletions (`POST /resource/bulk-delete`) in a single database transaction.
- **Request Context (`ctx`)**: Injected into all lifecycle hooks, permission checks, and `@action`s, providing access to `ctx.user`, `ctx.session`, `ctx.parent`, `ctx.action`, and request-scoped state.
- **Action Verbs & Universal Synonyms**: Native support for `list`, `retrieve`, `create`, `update`, `partial_update`, and `delete` (with aliases `get`, `post`, `put`, `patch`, `destroy`).
- **Flexible Schema Overrides**: Granular `schema_overrides` supporting request vs. response schemas (`req` / `res`), action-specific schemas, and tuples.
- **Smart PK & Fallback Lookup**: Support for primary key (`pk_field`, `pk_type`) and secondary fallback (`fallback_pk`, `fallback_type`), e.g., lookup by `id` or `slug` without query parameters.
- **Custom Routes & Actions (`@action` / `@route`)**: Add custom collection or detail endpoints with `@action(methods=['POST'], detail=True)` or `@route(...)`. Supports granular schema validation (`schema=...`, `validate_schema=True/False`, `response_schema=...`), automatically deserializing request data and serializing responses.
- **Filtering, Searching, & Pagination**: Built-in query filters (`__gte`, `__lte`, `__in`, etc.), multi-field text search (`?q=...`), and pagination envelopes.
- **Lifecycle Hooks**: Pure OOP hooks (`before_create_load`, `after_create_load`, `before_create`, `after_create`, `before_update_load`, `after_update_load`, `before_update(partial=...)`, `after_update(partial=...)`, `before_delete`, `after_delete`).
- **Authorization Bridge**: First-class integration with `flask-user-auth` or standalone token authentication via `ApiAuth`.
- **OpenAPI / Swagger Generation**: Interactive documentation out of the box.

## Quickstart

```python
from flask import Flask, Blueprint
from flask_build_api import Api, ApiMixin, action, route

app = Flask(__name__)
api_bp = Blueprint("api", __name__, url_prefix="/api")
api = Api(api_bp)

# Approach 1: Class-based Resource with Hooks & @route
class ProductApi(ApiMixin):
    model = Product
    schema = ProductSchema
    name = "products"
    pk_field = "id"
    pk_type = "int"
    fallback_pk = "slug"
    fallback_type = "string"
    filter_fields = ["category_id", "is_active"]
    search_fields = ["name", "sku", "description"]

    def before_create(self, instance, ctx=None):
        instance.created_by = getattr(ctx.user, "id", None)

    def before_update(self, instance, partial=False, ctx=None):
        if not partial:
            instance.version += 1

    @route(methods=["POST"], detail=True, url_path="restock", schema=RestockSchema, validate_schema=True)
    def restock(self, instance, data=None):
        instance.stock += data["quantity"]
        return {"status": "restocked", "new_stock": instance.stock}

api.register_resource(ProductApi)

# Approach 2: Declarative Factory Resource
user_api = api.create_resource(
    model=User,
    schema=UserSchema,
    name="users",
    filter_fields=["is_active", "is_admin"],
    sort_fields=["created_at"],
    paginate=True
)

# Approach 3: Nested Sub-Resource
# Mounts /api/users/<user_id>/orders and /api/users/<user_id>/orders/<order_id>
# Both user_api.create_resource and user_api.create_nested_resource work identically:
order_api = user_api.create_resource(
    model=Order,
    schema=OrderSchema,
    name="orders",
    parent_fk_field="user_id", # Foreign key on Order
    # Optional custom parent lookup resolver:
    resolve_parent={"user_id": lambda uid: db.session.get(User, uid)},
)

# Nested-resource naming:
#   parent_fk_field   -> FK column on the CHILD (auto-detected when omitted)
#   parent_url_param  -> URL variable name (defaults to the FK name, e.g. "user_id")
#   resolve_parent    -> optional callable/dict resolving the parent object
# Deprecated aliases parent_pk_field, parent_param and resolve_parent_pk still work.

# Approach 4: Singleton Resources (Standalone & Nested 1-to-1)
# Standalone: Mounts /api/settings (GET, POST, PUT, PATCH, DELETE) without {id}
settings_api = api.create_resource(
    model=Settings,
    schema=SettingsSchema,
    name="settings",
    singleton=True,
)

# Nested 1-to-1: Mounts /api/users/<user_id>/profile without {profile_id}
# Automatically scopes query to user_id, injects user_id on POST, and returns 409 Conflict if already created
profile_api = user_api.create_resource(
    model=Profile,
    schema=ProfileSchema,
    name="profile",
    singleton=True,
    parent_fk_field="user_id",
)

# Approach 5: Auto-SQLAlchemy Marshmallow Schemas
from flask_build_api import SchemaMixin, fields

# 5a. Declarative Schema with Relationships & Depth
class UserSchema(SchemaMixin):
    model = User
    fields = ["id", "name", "email", "orders"]
    load_only = ["password"]
    dump_only = ["id", "created_at"]
    relationships = {
        "orders": {"only": ["id", "total"], "write": False}
    }
    depth = 1
    custom_fields = {
        "display_name": fields.Method("get_display_name")
    }

    def get_display_name(self, obj):
        return f"User: {obj.name}"

# 5b. Functional Factory
OrderSchema = api.create_schema(
    model=Order,
    exclude=["internal_notes"],
    relationships={"items": {"only": ["id", "quantity", "price"]}},
    depth=1,
)

# 5c. Completely Zero-Boilerplate Resource (schema omitted!)
# If schema is omitted, api.create_resource automatically derives it from Item model
item_api = api.create_resource(model=Item)

# 5d. Self-Referential Recursive Schemas (e.g. Category Trees)
# Pattern A: Unbounded full-tree recursion (recurses down to database leaves)
class FullCategorySchema(SchemaMixin):
    model = Category
    fields = ["id", "name", "children"]
    recurse = ["children"]

# Pattern B: Bounded depth recursion (stops at exactly N levels)
class TwoLevelCategorySchema(SchemaMixin):
    model = Category
    fields = ["id", "name", "children"]
    recurse = ["children"]
    depth = 2  # Recursion cuts off at 2 levels deep!

# Or via factory with integer recurse depth:
BoundedCategorySchema = api.create_schema(Category, recurse=["children"], depth=2)

app.register_blueprint(api_bp)
```

## Lifecycle Hooks Reference

All hooks use clean, streamlined signatures. Contextual data such as `parent`, `parent_id`, `user`, `session`, `action`, `request`, and request state are accessed via `ctx`:

| Hook | Signature | Description |
|------|-----------|-------------|
| `before_create_load` | `(data, ctx=None)` | Pre-process raw JSON before Marshmallow loads it. Returns modified `data`. |
| `after_create_load` | `(data, ctx=None)` | Modify deserialized data or model before persistence. Returns loaded object. |
| `before_create` | `(instance, ctx=None)` | Called immediately before `session.add(instance)` & commit. |
| `after_create` | `(instance, ctx=None)` | Called immediately after successful commit. |
| `before_update_load` | `(data, instance, partial=False, ctx=None)` | Pre-process raw JSON before loading update payload. |
| `after_update_load` | `(data, instance, partial=False, ctx=None)` | Modify deserialized data before applying to `instance`. |
| `before_update` | `(instance, partial=False, ctx=None)` | Called before committing updates (`partial=True` on PATCH, `False` on PUT). |
| `after_update` | `(instance, partial=False, ctx=None)` | Called after commit of updates. |
| `before_delete` | `(instance, ctx=None)` | Called before deleting record from session (or soft-deleting). |
| `after_delete` | `(instance, ctx=None)` | Called after record is deleted & committed. |
| `check_permissions` | `(action, instance=None, ctx=None)` | Enforce authorization rules. Raise `ForbiddenError` if denied. |

---

## Native Token Authentication (`TokenAuthManager` & `flask_build_api.token`)

`flask-build-api` includes built-in JWT + HttpOnly refresh cookie authentication designed for modern single-page apps, mobile clients, and REST APIs.

This is an optional extra. Install it with `pip install "flask-build-api[auth]"`. The auth modules are lazy-loaded, so `import flask_build_api` does not require `pyjwt` or `flask-httpauth` unless you actually use the auth suite.

### 1. `UserModelMixin` / `TokenUserMixin`

Add password hashing, verification, and serialization exclusion to your User model with zero boilerplate:

```python
from flask_build_api import UserModelMixin
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
import sqlalchemy as sa

class User(Base, UserModelMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    email: Mapped[str] = mapped_column(sa.String(120), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    is_admin: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True)

# Usage:
user = User(email="jane@example.com")
user.password = "MySecurePassword"   # Automatically hashed into password_hash
assert user.check_password("MySecurePassword") is True
```

### 2. Initializing `TokenAuthManager`

Attach to your Flask app or `Api`:

```python
from flask_build_api import Api, TokenAuthManager

auth = TokenAuthManager(
    model=User,
    identity_field="email",
    password_field="password_hash",
    auto_mount=True,
    url_prefix="/auth",
)
auth.init_app(app)

# Or via api factory:
api = Api(app)
auth = api.create_auth(model=User)
```

Mounts `/auth/login`, `/auth/refresh`, `/auth/me`, and `/auth/logout` out of the box with HttpOnly cookie rotation.

### 3. Dedicated `security` (or `token`) Namespace

Protect routes using the dedicated `flask_build_api.security` namespace (or its alias `flask_build_api.token`), returning pure JSON 401/403 responses:

```python
from flask_build_api.security import (
    token_required,
    admin_required,
    login_required,
    current_user,
)

@app.route("/api/profile")
@token_required
def profile():
    return {"user": current_user.email}

@app.route("/api/admin/metrics")
@admin_required
def admin_metrics():
    return {"status": "ok"}
```

You can also access the namespace on your auth instance as `auth.security.token_required`, `auth.token.token_required`, `auth.security.current_user`, or top-level from `flask_build_api`.

---

## Auto OpenAPI / Swagger Docs & Standardized Error Responses

### 1. Zero-Boilerplate OpenAPI 3.0.0 & Swagger UI

Enable interactive API documentation by simply passing `docs=True` (or a custom path like `docs="/api/docs"`) when instantiating `Api`:

```python
api = Api(app, prefix="/api", docs=True, docs_url="/docs", docs_title="My Store API")
# Or mount explicitly:
# api.mount_docs(url_prefix="/docs", title="My Store API")
```

This mounts:
* **Interactive Swagger UI**: `GET /docs`
* **Full OpenAPI 3.0.0 JSON**: `GET /docs/openapi.json`

The documentation automatically includes:
* All collection, detail, singleton, and nested routes
* Search fields (`?q=...`), sort fields (`?sort=...`), filter fields, and pagination parameters
* Request bodies and responses
* Bearer JWT security tags (`bearerAuth`)
* Complete `ErrorResponse` schema component and standard HTTP error responses (400, 401, 403, 404, 412, 422, 500)

### 2. Standardized `error_responses` & Custom Declarations

All errors return a predictable JSON payload:

```json
{
  "success": false,
  "error": {
    "code": "validation_error",
    "message": "Validation error",
    "details": {
      "email": ["Invalid email address."]
    },
    "request_id": "03e48816-43b6-455b-b5cb-0fe4ecaa84b6"
  },
  "errors": {
    "email": ["Invalid email address."]
  }
}
```

You can define custom `error_responses` on your resource class, factory, or `@action`:

```python
# Resource-level custom errors
widget_api = api.create_resource(
    model=Widget,
    name="widgets",
    error_responses={409: "Conflict - duplicate widget name"},
)

# Action-level custom errors
@action(methods=["POST"], detail=True, url_path="restock", error_responses={429: "Restock rate limit exceeded"})
def restock(self, instance, data=None):
    ...
```

All `ApiError` exceptions (such as `NotFoundError`, `UnauthorizedError`, `ForbiddenError`, `ValidationErrorResponse`) raised anywhere in your app or custom routes are automatically caught and formatted via `register_error_handlers(app)`.

### 3. Config-Driven OpenAPI Specification (`app.config`)

You can declare all OpenAPI metadata and auto-mount behavior directly within your Flask application's `app.config` without passing arguments in Python code:

```python
app = Flask(__name__)

# Declare documentation metadata via Flask config
app.config["API_TITLE"] = "AutoParts Pro REST API"
app.config["API_VERSION"] = "2.1.0"
app.config["API_DESCRIPTION"] = "Full-featured auto parts catalog, cart, checkout, and inventory API."
app.config["API_URL"] = "https://api.buyautoparts.com/v1"  # Or list of servers
app.config["API_LICENSE"] = {"name": "MIT", "url": "https://opensource.org/licenses/MIT"}
app.config["API_TERMS_OF_SERVICE"] = "https://buyautoparts.com/terms"
app.config["API_CONTACT"] = {"name": "API Support", "email": "api@buyautoparts.com"}

# Configure API Key Security Scheme in Swagger UI
app.config["API_KEY"] = "X-API-KEY"       # Injects apiKeyAuth security scheme
app.config["API_KEY_NAME"] = "X-API-KEY"  # Header/Query param name
app.config["API_KEY_IN"] = "header"       # 'header' (default), 'query', or 'cookie'

# Auto-mount Swagger UI & OpenAPI JSON directly on init
app.config["API_DOCS_ENABLED"] = True     # Or app.config["API_DOCS"] = "/api-docs"
app.config["API_DOCS_URL"] = "/api-docs"  # Swagger UI endpoint (default: /docs)

api = Api(app)  # Reads all metadata and mounts Swagger automatically!
```

#### Supported Configuration Keys

| Config Key | OpenAPI Field | Description / Example |
|---|---|---|
| `API_TITLE` / `OPENAPI_TITLE` | `info.title` | Title of your API (defaults to `"API Documentation"`). |
| `API_VERSION` / `OPENAPI_VERSION` | `info.version` | Version string (defaults to `"1.0.0"`). |
| `API_DESCRIPTION` / `OPENAPI_DESCRIPTION` | `info.description` | Markdown description displayed at the top of Swagger UI. |
| `API_URL` / `API_SERVERS` | `servers` | Base URL string (e.g. `"https://api.example.com"`) or list of server objects (`[{"url": "...", "description": "..."}]`). |
| `API_LICENSE` / `OPENAPI_LICENSE` | `info.license` | License name (e.g. `"MIT"`) or dict (`{"name": "Apache 2.0", "url": "..."}`). |
| `API_KEY` / `API_KEY_NAME` | `components.securitySchemes.apiKeyAuth` | Enables API Key authentication scheme with specified header/query name. |
| `API_KEY_IN` | `components.securitySchemes.apiKeyAuth.in` | Location of API key: `"header"` (default), `"query"`, or `"cookie"`. |
| `API_TERMS_OF_SERVICE` | `info.termsOfService` | URL pointing to terms of service. |
| `API_CONTACT` | `info.contact` | Contact name string or dictionary (`{"name": "...", "email": "...", "url": "..."}`). |
| `API_DOCS_ENABLED` / `API_DOCS` | N/A | Boolean `True` or string path (e.g. `"/docs"`) to automatically mount Swagger UI. |
| `API_DOCS_URL` | N/A | URL prefix for the Swagger UI documentation (defaults to `"/docs"`). |




