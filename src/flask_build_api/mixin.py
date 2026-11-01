import inspect
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
from flask import current_app, jsonify, request
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
import sqlalchemy as sa

from .conditional import generate_etag, match_etag
from .context import ApiContext
from .errors import ApiError, NotFoundError, PreconditionFailedError, format_error_response
from .idempotency import IdempotencyStore, MemoryIdempotencyStore
from .query import apply_filters, apply_search, apply_sorting, paginate_query
from .schema import SchemaResolver
from .utils import cast_identifier, is_type_match, normalize_action


class ApiMixin:
    """Base mixin providing CRUD actions, query building, schema validation,
    lifecycle hooks, smart primary-key resolution, context injection, ETags,
    idempotency, and nested sub-resource support.
    """

    api: Any = None
    ctx: Optional[ApiContext] = None

    # Model & Schemas
    model: Any = None
    schema: Any = None
    schema_overrides: Dict[str, Any] = {}

    # Resource Identification
    name: str = ""
    pk_field: str = "id"
    pk_type: str = "int"
    fallback_pk: Optional[str] = None
    fallback_type: str = "string"
    allowed_lookup_fields: Optional[List[str]] = None

    # Nested Sub-resource Attributes
    is_nested: bool = False
    parent_resource: Any = None
    parent_pk_field: Optional[str] = None
    parent_param: str = "parent_id"
    resolve_parent_pk: Optional[Union[Callable[[Any], Any], Dict[str, Callable[[Any], Any]]]] = None

    # Supported Actions
    methods: Union[List[str], Tuple[str, ...]] = (
        "list",
        "retrieve",
        "create",
        "update",
        "partial_update",
        "delete",
        "bulk_create",
        "bulk_delete",
    )

    # Pagination & Enveloping
    paginate: bool = True
    per_page: int = 20
    max_per_page: int = 100
    allow_disable_pagination: bool = True
    max_unpaginated_limit: Optional[int] = 1000
    envelope_key: Optional[str] = "data"
    data_envelope_key: Optional[str] = None  # Alias for envelope_key
    items_key: str = "items"

    # Query Capabilities
    filter_fields: List[str] = []
    search_fields: List[str] = []
    sort_fields: List[str] = []
    default_sort_field: Optional[str] = None

    # Soft Delete
    soft_delete: bool = False
    soft_delete_field: str = "is_deleted"

    # Singleton Configuration
    singleton: bool = False
    singleton_pk: Optional[Any] = None
    resolve_singleton: Optional[Callable[[Any], Any]] = None

    # Production Integrity: ETags & Idempotency
    enable_etags: bool = True
    idempotent_actions: List[str] = ["create", "bulk_create"]
    idempotency_store: Optional[IdempotencyStore] = None

    # Authorization & Decorators
    auth_required: bool = False
    auth_actions: Dict[str, Any] = {}
    decorators: List[Any] = []
    method_decorators: Dict[str, List[Any]] = {}

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)
        self.schema_resolver = SchemaResolver(
            default_schema=self.schema,
            schema_overrides=self.schema_overrides,
        )
        if self.idempotency_store is None:
            self.idempotency_store = MemoryIdempotencyStore()

    # --- Nested Resource Factory Helper -------------------------------------

    def create_nested_resource(
        self,
        model: Any,
        schema: Optional[Any] = None,
        name: str = "",
        parent_pk_field: Optional[str] = None,
        resolve_parent_pk: Optional[Union[Callable, Dict[str, Callable]]] = None,
        **kwargs
    ) -> "ApiMixin":
        """Mount a child subresource directly under this resource."""
        if self.api is None:
            raise RuntimeError("Cannot create nested resource on an unregistered ApiMixin instance.")
        return self.api.create_nested_resource(
            parent=self,
            model=model,
            schema=schema,
            name=name,
            parent_pk_field=parent_pk_field,
            resolve_parent_pk=resolve_parent_pk,
            **kwargs
        )

    def create_resource(
        self,
        model: Any,
        schema: Optional[Any] = None,
        name: str = "",
        parent_pk_field: Optional[str] = None,
        resolve_parent_pk: Optional[Union[Callable, Dict[str, Callable]]] = None,
        **kwargs
    ) -> "ApiMixin":
        """Alias for create_nested_resource when invoked on an existing resource instance."""
        return self.create_nested_resource(
            model=model,
            schema=schema,
            name=name,
            parent_pk_field=parent_pk_field,
            resolve_parent_pk=resolve_parent_pk,
            **kwargs
        )

    # --- Parent Instance Resolution -----------------------------------------

    def get_parent_object(self, parent_id: Optional[Any] = None) -> Optional[Any]:
        """Resolve the parent model instance using resolve_parent_pk or parent_resource."""
        if parent_id is None or not self.is_nested:
            return None

        if self.resolve_parent_pk is not None:
            if callable(self.resolve_parent_pk):
                parent = self.resolve_parent_pk(parent_id)
            elif isinstance(self.resolve_parent_pk, dict):
                resolver = (
                    self.resolve_parent_pk.get(self.parent_param)
                    or self.resolve_parent_pk.get(self.parent_pk_field)
                    or next(iter(self.resolve_parent_pk.values()), None)
                )
                parent = resolver(parent_id) if callable(resolver) else None
            else:
                parent = None

            if parent is None:
                raise NotFoundError(f"Parent resource ({self.parent_param}) not found")
            return parent

        if self.parent_resource is not None and hasattr(self.parent_resource, "get_object"):
            return self.parent_resource.get_object(parent_id)

        return None

    def get_parent(self, parent_id: Optional[Any] = None) -> Optional[Any]:
        """Convenient alias for get_parent_object."""
        return self.get_parent_object(parent_id)

    # --- Database Session ----------------------------------------------------

    def get_session(self) -> Any:
        """Resolve current SQLAlchemy session."""
        if hasattr(self, "session") and self.session:
            return self.session
        sqla = current_app.extensions.get("sqlalchemy")
        if sqla and hasattr(sqla, "session"):
            return sqla.session
        if hasattr(self.model, "query") and hasattr(self.model.query, "session"):
            return self.model.query.session
        raise RuntimeError("Could not resolve SQLAlchemy session from Flask app.")

    # --- Schema Helpers ------------------------------------------------------

    def load(self, data: Any, action: str, instance: Optional[Any] = None, partial: bool = False) -> Any:
        return self.schema_resolver.load(
            data,
            action=action,
            instance=instance,
            partial=partial,
            session=self.get_session(),
        )

    def dump(self, obj: Any, action: str, many: bool = False) -> Any:
        return self.schema_resolver.dump(obj, action=action, many=many)

    def envelope(self, data: Any, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Wrap payload in envelope dictionary."""
        payload: Dict[str, Any] = {"success": True}
        active_key = self.data_envelope_key if self.data_envelope_key is not None else self.envelope_key

        if active_key:
            payload[active_key] = data
        else:
            if isinstance(data, dict):
                payload.update(data)
            else:
                payload["data"] = data

        if meta is not None:
            payload["meta"] = meta
        return payload

    # --- Context Helper ------------------------------------------------------

    def make_context(
        self,
        action: str,
        identifier: Optional[Any] = None,
        instance: Optional[Any] = None,
        parent_id: Optional[Any] = None,
        parent: Optional[Any] = None,
        **kwargs
    ) -> ApiContext:
        req_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        ctx = ApiContext(
            action=action,
            resource=self,
            api=self.api,
            identifier=identifier,
            instance=instance,
            parent_id=parent_id,
            parent=parent,
            request_id=req_id,
            **kwargs,
        )
        self.ctx = ctx
        return ctx

    # --- Hook Invocation Dispatcher -----------------------------------------

    def _call_hook(self, hook_name: str, *args, **kwargs) -> Any:
        hook = getattr(self, hook_name, None)
        if hook is None:
            return args[0] if args else None

        try:
            sig = inspect.signature(hook)
            accepts_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
            call_kwargs = {}
            ctx = kwargs.get("ctx")
            for k, v in kwargs.items():
                if accepts_var_kw or k in sig.parameters:
                    call_kwargs[k] = v
            # If a custom hook function explicitly requests 'parent' or 'parent_id', resolve from ctx
            if ctx is not None:
                if "parent" in sig.parameters and "parent" not in call_kwargs:
                    call_kwargs["parent"] = ctx.parent
                if "parent_id" in sig.parameters and "parent_id" not in call_kwargs:
                    call_kwargs["parent_id"] = ctx.parent_id
            return hook(*args, **call_kwargs)
        except (ValueError, TypeError):
            return hook(*args)

    # --- Object Resolution & Queries -----------------------------------------

    def get_query(
        self,
        action: str = "list",
        parent_id: Optional[Any] = None,
        parent: Optional[Any] = None,
        ctx: Optional[ApiContext] = None,
    ) -> Any:
        """Return base SQLAlchemy query for the model. Scopes to parent_id if nested."""
        query = self.model.query if hasattr(self.model, "query") else None
        if query is None:
            session = self.get_session()
            query = session.query(self.model)

        if self.soft_delete and hasattr(self.model, self.soft_delete_field):
            query = query.filter(getattr(self.model, self.soft_delete_field) == False)

        if self.is_nested and parent_id is not None and self.parent_pk_field:
            col = getattr(self.model, self.parent_pk_field, None)
            if col is not None:
                target_val = getattr(parent, "id", parent_id) if parent else parent_id
                query = query.filter(col == target_val)

        return query

    def get_object(
        self,
        identifier: Optional[Any] = None,
        action: str = "retrieve",
        parent_id: Optional[Any] = None,
        parent: Optional[Any] = None,
        ctx: Optional[ApiContext] = None,
    ) -> Any:
        """Resolve model instance using primary key, fallback key, or singleton resolution."""
        query = self.get_query(action=action, parent_id=parent_id, parent=parent, ctx=ctx)

        # Singleton Resolution
        if self.singleton:
            if self.resolve_singleton is not None and callable(self.resolve_singleton):
                instance = self.resolve_singleton(ctx)
            elif self.is_nested and parent_id is not None and self.parent_pk_field:
                instance = query.first()
            elif self.singleton_pk is not None:
                col = getattr(self.model, self.pk_field, None)
                instance = query.filter(col == self.singleton_pk).first() if col is not None else query.first()
            else:
                instance = query.first()

            if instance is None:
                raise NotFoundError(f"{self.name.capitalize() or 'Record'} not found")
            self.check_permissions(action, instance=instance, ctx=ctx)
            return instance

        by_param = request.args.get("by")
        if by_param and self.allowed_lookup_fields and by_param in self.allowed_lookup_fields:
            col = getattr(self.model, by_param, None)
            if col is not None:
                instance = query.filter(col == identifier).first()
                if instance:
                    self.check_permissions(action, instance=instance, ctx=ctx)
                    return instance

        if is_type_match(identifier, self.pk_type):
            casted, success = cast_identifier(identifier, self.pk_type)
            if success:
                col = getattr(self.model, self.pk_field, None)
                if col is not None:
                    instance = query.filter(col == casted).first()
                    if instance:
                        self.check_permissions(action, instance=instance, ctx=ctx)
                        return instance

        if self.fallback_pk and is_type_match(identifier, self.fallback_type):
            casted, success = cast_identifier(identifier, self.fallback_type)
            if success:
                col = getattr(self.model, self.fallback_pk, None)
                if col is not None:
                    instance = query.filter(col == casted).first()
                    if instance:
                        self.check_permissions(action, instance=instance, ctx=ctx)
                        return instance

        raise NotFoundError(f"{self.name.capitalize() or 'Record'} not found")

    # --- Permissions & Lifecycle Hooks ---------------------------------------

    def check_permissions(
        self,
        action: str,
        instance: Optional[Any] = None,
        ctx: Optional[ApiContext] = None,
        **kwargs
    ) -> None:
        pass

    def before_create_load(self, data: Dict[str, Any], ctx: Optional[ApiContext] = None) -> Dict[str, Any]:
        """Pre-process or sanitize raw request dictionary before Marshmallow loading."""
        return data

    def after_create_load(self, data: Any, ctx: Optional[ApiContext] = None) -> Any:
        """Modify loaded data/model instance before database insertion."""
        return data

    def before_create(self, instance: Any, ctx: Optional[ApiContext] = None) -> None:
        """Pre-commit hook called immediately before db session add & commit on creation."""
        pass

    def after_create(self, instance: Any, ctx: Optional[ApiContext] = None) -> None:
        """Post-commit hook called immediately after successful database commit on creation."""
        pass

    def before_update_load(
        self,
        data: Dict[str, Any],
        instance: Any,
        partial: bool = False,
        ctx: Optional[ApiContext] = None,
    ) -> Dict[str, Any]:
        """Pre-process raw request dictionary before updating."""
        return data

    def after_update_load(
        self,
        data: Any,
        instance: Any,
        partial: bool = False,
        ctx: Optional[ApiContext] = None,
    ) -> Any:
        """Modify loaded data before applying attributes."""
        return data

    def before_update(self, instance: Any, partial: bool = False, ctx: Optional[ApiContext] = None) -> None:
        """Pre-commit hook called immediately before committing updates."""
        pass

    def after_update(self, instance: Any, partial: bool = False, ctx: Optional[ApiContext] = None) -> None:
        """Post-commit hook called immediately after committing updates."""
        pass

    def before_delete(self, instance: Any, ctx: Optional[ApiContext] = None) -> None:
        """Pre-commit hook called before deleting record or marking soft-deleted."""
        pass

    def after_delete(self, instance: Any, ctx: Optional[ApiContext] = None) -> None:
        """Post-commit hook called after record is deleted."""
        pass

    def filter_query(self, query: Any, ctx: Optional[ApiContext] = None) -> Any:
        """Hook to apply custom query filters or augmentations for collection queries."""
        return query

    # --- CRUD Actions --------------------------------------------------------

    def list(self, parent_id: Optional[Any] = None) -> Any:
        parent = self.get_parent_object(parent_id)
        ctx = self.make_context("list", parent_id=parent_id, parent=parent)
        self.check_permissions("list", ctx=ctx)

        query = self.get_query("list", parent_id=parent_id, parent=parent, ctx=ctx)
        query = apply_filters(query, self.model, self.filter_fields)
        query = apply_search(query, self.model, self.search_fields)
        query = apply_sorting(query, self.model, self.sort_fields, self.default_sort_field)
        query = self._call_hook("filter_query", query, ctx=ctx)

        meta = None
        if self.paginate:
            items, meta = paginate_query(
                query,
                per_page=self.per_page,
                max_per_page=self.max_per_page,
                allow_disable_pagination=self.allow_disable_pagination,
                max_unpaginated_limit=self.max_unpaginated_limit,
            )
        else:
            items = query.all() if hasattr(query, "all") else query

        serialized = self.dump(items, action="list", many=True)
        resp_data = self.envelope(serialized, meta=meta)
        headers = {"X-Request-ID": ctx.request_id}
        return jsonify(resp_data), 200, headers

    def retrieve(self, identifier: Optional[Any] = None, parent_id: Optional[Any] = None) -> Any:
        parent = self.get_parent_object(parent_id)
        ctx = self.make_context("retrieve", identifier=identifier, parent_id=parent_id, parent=parent)
        instance = self.get_object(identifier, action="retrieve", parent_id=parent_id, parent=parent, ctx=ctx)
        ctx.instance = instance

        # ETag calculation & If-None-Match 304 handling
        etag = generate_etag(instance) if self.enable_etags else None
        headers = {"X-Request-ID": ctx.request_id}
        if etag:
            headers["ETag"] = etag
            if match_etag(request.headers.get("If-None-Match"), etag):
                return "", 304, headers

        serialized = self.dump(instance, action="retrieve")
        return jsonify(self.envelope(serialized)), 200, headers

    def create(self, parent_id: Optional[Any] = None) -> Any:
        parent = self.get_parent_object(parent_id)
        ctx = self.make_context("create", parent_id=parent_id, parent=parent)
        self.check_permissions("create", ctx=ctx)

        # Singleton guard: 409 Conflict if instance already exists
        if self.singleton:
            try:
                existing = self.get_object(action="retrieve", parent_id=parent_id, parent=parent, ctx=ctx)
                if existing is not None:
                    raise ApiError("Singleton record already exists", status_code=409, code="conflict")
            except NotFoundError:
                pass

        # Idempotency check
        idempotency_key = request.headers.get("Idempotency-Key")
        if idempotency_key and self.idempotency_store and "create" in self.idempotent_actions:
            cached = self.idempotency_store.get(idempotency_key)
            if cached is not None:
                cached_body, cached_status, cached_headers = cached
                return jsonify(cached_body), cached_status, cached_headers

        raw_data = request.get_json(silent=True) or {}
        if self.is_nested and parent_id is not None and self.parent_pk_field:
            target_val = getattr(parent, "id", parent_id) if parent else parent_id
            if self.parent_pk_field not in raw_data:
                raw_data[self.parent_pk_field] = target_val

        raw_data = self._call_hook("before_create_load", raw_data, ctx=ctx)

        loaded = self.load(raw_data, action="create")
        loaded = self._call_hook("after_create_load", loaded, ctx=ctx)

        instance = loaded if isinstance(loaded, self.model) else self.model(**loaded)
        ctx.instance = instance

        if self.is_nested and parent_id is not None and self.parent_pk_field:
            target_val = getattr(parent, "id", parent_id) if parent else parent_id
            setattr(instance, self.parent_pk_field, target_val)

        session = self.get_session()
        try:
            self._call_hook("before_create", instance, ctx=ctx)
            session.add(instance)
            session.commit()
            self._call_hook("after_create", instance, ctx=ctx)
        except (IntegrityError, SQLAlchemyError) as e:
            session.rollback()
            raise ApiError(f"Database error saving record: {str(e)}", status_code=400, code="db_error")

        serialized = self.dump(instance, action="create")
        resp_data = self.envelope(serialized)
        headers = {"X-Request-ID": ctx.request_id}
        if self.enable_etags:
            headers["ETag"] = generate_etag(instance)

        if idempotency_key and self.idempotency_store and "create" in self.idempotent_actions:
            self.idempotency_store.set(idempotency_key, (resp_data, 201, headers))

        return jsonify(resp_data), 201, headers

    def update(self, identifier: Optional[Any] = None, parent_id: Optional[Any] = None) -> Any:
        return self._do_update(identifier, partial=False, parent_id=parent_id)

    def partial_update(self, identifier: Optional[Any] = None, parent_id: Optional[Any] = None) -> Any:
        return self._do_update(identifier, partial=True, parent_id=parent_id)

    def _do_update(self, identifier: Optional[Any] = None, partial: bool = False, parent_id: Optional[Any] = None) -> Any:
        action_name = "partial_update" if partial else "update"
        parent = self.get_parent_object(parent_id)
        ctx = self.make_context(action_name, identifier=identifier, parent_id=parent_id, parent=parent)
        instance = self.get_object(identifier, action=action_name, parent_id=parent_id, parent=parent, ctx=ctx)
        ctx.instance = instance
        self.check_permissions(action_name, instance=instance, ctx=ctx)

        # Optimistic concurrency via If-Match ETag check
        if self.enable_etags and request.headers.get("If-Match"):
            current_etag = generate_etag(instance)
            if not match_etag(request.headers.get("If-Match"), current_etag):
                raise PreconditionFailedError("Resource has been modified concurrently. Please reload and try again.")

        raw_data = request.get_json(silent=True) or {}
        raw_data = self._call_hook(
            "before_update_load",
            raw_data,
            instance=instance,
            partial=partial,
            ctx=ctx,
        )

        loaded = self.load(raw_data, action=action_name, instance=instance, partial=partial)
        loaded = self._call_hook(
            "after_update_load",
            loaded,
            instance=instance,
            partial=partial,
            ctx=ctx,
        )

        if isinstance(loaded, dict):
            for k, v in loaded.items():
                setattr(instance, k, v)

        session = self.get_session()
        try:
            self._call_hook("before_update", instance, partial=partial, ctx=ctx)
            session.commit()
            self._call_hook("after_update", instance, partial=partial, ctx=ctx)
        except (IntegrityError, SQLAlchemyError) as e:
            session.rollback()
            raise ApiError(f"Database error updating record: {str(e)}", status_code=400, code="db_error")

        serialized = self.dump(instance, action=action_name)
        headers = {"X-Request-ID": ctx.request_id}
        if self.enable_etags:
            headers["ETag"] = generate_etag(instance)

        return jsonify(self.envelope(serialized)), 200, headers

    def delete(self, identifier: Optional[Any] = None, parent_id: Optional[Any] = None) -> Any:
        parent = self.get_parent_object(parent_id)
        ctx = self.make_context("delete", identifier=identifier, parent_id=parent_id, parent=parent)
        instance = self.get_object(identifier, action="delete", parent_id=parent_id, parent=parent, ctx=ctx)
        ctx.instance = instance
        self.check_permissions("delete", instance=instance, ctx=ctx)

        session = self.get_session()
        try:
            self._call_hook("before_delete", instance, ctx=ctx)
            if self.soft_delete and hasattr(instance, self.soft_delete_field):
                setattr(instance, self.soft_delete_field, True)
            else:
                session.delete(instance)
            session.commit()
            self._call_hook("after_delete", instance, ctx=ctx)
        except (IntegrityError, SQLAlchemyError) as e:
            session.rollback()
            raise ApiError(f"Database error deleting record: {str(e)}", status_code=400, code="db_error")

        headers = {"X-Request-ID": ctx.request_id}
        return jsonify({"success": True, "message": "Record deleted successfully"}), 200, headers

    # --- Bulk Actions --------------------------------------------------------

    def bulk_create(self, parent_id: Optional[Any] = None) -> Any:
        """Batch create multiple records in a single database transaction."""
        parent = self.get_parent_object(parent_id)
        ctx = self.make_context("bulk_create", parent_id=parent_id, parent=parent)
        self.check_permissions("bulk_create", ctx=ctx)

        payload = request.get_json(silent=True) or []
        if not isinstance(payload, list):
            raise ApiError("Payload for bulk_create must be a JSON array of items.", status_code=400)

        created_instances = []
        session = self.get_session()

        try:
            for item_data in payload:
                item_data = self._call_hook("before_create_load", item_data, ctx=ctx)
                loaded = self.load(item_data, action="bulk_create")
                loaded = self._call_hook("after_create_load", loaded, ctx=ctx)
                inst = loaded if isinstance(loaded, self.model) else self.model(**loaded)
                if self.is_nested and parent_id is not None and self.parent_pk_field:
                    target_val = getattr(parent, "id", parent_id) if parent else parent_id
                    setattr(inst, self.parent_pk_field, target_val)
                self._call_hook("before_create", inst, ctx=ctx)
                session.add(inst)
                created_instances.append(inst)

            session.commit()
            for inst in created_instances:
                self._call_hook("after_create", inst, ctx=ctx)
        except (IntegrityError, SQLAlchemyError) as e:
            session.rollback()
            raise ApiError(f"Database error in bulk create: {str(e)}", status_code=400, code="db_error")

        serialized = self.dump(created_instances, action="bulk_create", many=True)
        headers = {"X-Request-ID": ctx.request_id}
        return jsonify(self.envelope(serialized)), 201, headers

    def bulk_delete(self, parent_id: Optional[Any] = None) -> Any:
        """Batch delete multiple records in a single database transaction."""
        parent = self.get_parent_object(parent_id)
        ctx = self.make_context("bulk_delete", parent_id=parent_id, parent=parent)
        self.check_permissions("bulk_delete", ctx=ctx)

        payload = request.get_json(silent=True) or {}
        ids = payload.get("ids", []) if isinstance(payload, dict) else payload
        if not ids or not isinstance(ids, list):
            raise ApiError("Payload must include an 'ids' array.", status_code=400)

        col = getattr(self.model, self.pk_field, None)
        if col is None:
            raise ApiError(f"Model has no primary key column '{self.pk_field}'.", status_code=500)

        query = self.get_query("bulk_delete", parent_id=parent_id, parent=parent, ctx=ctx)
        instances = query.filter(col.in_(ids)).all()

        session = self.get_session()
        try:
            for inst in instances:
                self._call_hook("before_delete", inst, ctx=ctx)
                if self.soft_delete and hasattr(inst, self.soft_delete_field):
                    setattr(inst, self.soft_delete_field, True)
                else:
                    session.delete(inst)

            session.commit()
            for inst in instances:
                self._call_hook("after_delete", inst, ctx=ctx)
        except (IntegrityError, SQLAlchemyError) as e:
            session.rollback()
            raise ApiError(f"Database error in bulk delete: {str(e)}", status_code=400, code="db_error")

        headers = {"X-Request-ID": ctx.request_id}
        return jsonify({"success": True, "count": len(instances), "message": f"{len(instances)} records deleted."}), 200, headers
