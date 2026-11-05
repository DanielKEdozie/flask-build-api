import inspect
from functools import wraps
from typing import Any, Callable, Dict, List, Optional, Tuple, Type, Union
from flask import Blueprint, Flask, Response, current_app, jsonify, request
from marshmallow import ValidationError
from werkzeug.exceptions import HTTPException
import sqlalchemy as sa

from .auth import ApiAuth
from .auto_schema import SchemaMixin, create_schema
from .errors import ApiError, ValidationErrorResponse, format_error_response
from .mixin import ApiMixin
from .plugins import PluginRegistry, default_registry
from .token_auth import TokenAuthManager
from .utils import normalize_action, resolve_deprecated_alias


class Api:
    """The central API manager. Can be attached to a Flask App or a Blueprint."""

    def __init__(
        self,
        app_or_blueprint: Optional[Union[Flask, Blueprint]] = None,
        prefix: str = "",
        envelope_key: Optional[str] = "data",
        data_envelope_key: Optional[str] = None,
        items_key: str = "items",
        auth: Optional[Union[Dict[str, Any], Any]] = None,
        docs: Union[bool, str] = False,
        docs_url: str = "/docs",
        docs_title: str = "API Documentation",
        register_errors: bool = True,
    ):
        self.target = app_or_blueprint
        self.prefix = prefix.rstrip("/")
        self.envelope_key = data_envelope_key if data_envelope_key is not None else envelope_key
        self.data_envelope_key = self.envelope_key
        self.items_key = items_key
        self.auth: Optional[ApiAuth] = None
        self.plugins = PluginRegistry()
        self.resources: Dict[str, ApiMixin] = {}
        self.docs = docs
        self.docs_url = docs_url
        self.docs_title = docs_title
        self.register_errors = register_errors

        if auth is not None:
            if isinstance(auth, dict):
                self.create_auth(**auth)
            else:
                self.configure_auth(auth)

        if app_or_blueprint is not None:
            self.init_app(app_or_blueprint)

    def init_app(self, app_or_blueprint: Union[Flask, Blueprint]) -> None:
        self.target = app_or_blueprint
        if hasattr(app_or_blueprint, "extensions"):
            app_or_blueprint.extensions["build_api"] = self

        cfg = getattr(app_or_blueprint, "config", {})
        if not cfg and hasattr(app_or_blueprint, "app"):
            cfg = getattr(app_or_blueprint.app, "config", {})

        if self.register_errors:
            from .errors import register_error_handlers
            register_error_handlers(app_or_blueprint)
        if self.auth and hasattr(self.auth, "backend") and hasattr(self.auth.backend, "mount"):
            if getattr(self.auth.backend, "auto_mount", True):
                self.auth.backend.mount(app_or_blueprint)
        elif self.auth and hasattr(self.auth, "mount") and getattr(self.auth, "auto_mount", True):
            self.auth.mount(app_or_blueprint)

        # Docs auto-activation via config or self.docs
        docs_config = cfg.get("API_DOCS") if cfg.get("API_DOCS") is not None else (
            cfg.get("API_DOCS_ENABLED") or cfg.get("OPENAPI_ENABLED")
        )
        should_mount_docs = bool(self.docs or docs_config)

        if should_mount_docs:
            url = (
                self.docs
                if isinstance(self.docs, str)
                else (
                    docs_config
                    if isinstance(docs_config, str)
                    else (cfg.get("API_DOCS_URL") or self.docs_url)
                )
            )
            title = cfg.get("API_TITLE") or self.docs_title
            self.mount_docs(url_prefix=url, title=title)

    def create_auth(self, **kwargs) -> TokenAuthManager:
        """Create and attach a native TokenAuthManager with HttpOnly cookie refresh."""
        mgr = TokenAuthManager(**kwargs)
        self.configure_auth(mgr)
        if self.target and mgr.auto_mount:
            mgr.mount(self.target)
        return mgr

    def extend(self, *args, **kwargs) -> "Api":
        """Register capability providers or adapters with the API.

        Examples:
            api.extend("auth", auth)
            api.extend("storage", storage)
            api.extend("tasks", celery_adapter)
            api.extend("payment", paystack_adapter)
            api.extend("catalog", fb_catalog_adapter)
            api.extend(auth=auth, storage=storage, tasks=celery)
            api.extend(CeleryAdapter())
        """
        # Form 1: api.extend("name", provider)
        if len(args) == 2 and isinstance(args[0], str):
            self._register_plugin(args[0], args[1])

        # Form 2: api.extend(AdapterInstance()) -> auto infer name from class
        elif len(args) == 1 and not kwargs:
            provider = args[0]
            name = getattr(provider, "name", None) or getattr(provider, "plugin_name", None)
            if not name:
                cls_name = provider.__class__.__name__.lower()
                for suffix in ["adapter", "plugin", "extension", "service"]:
                    if cls_name.endswith(suffix) and len(cls_name) > len(suffix):
                        cls_name = cls_name[:-len(suffix)]
                        break
                name = cls_name
            self._register_plugin(name, provider)

        # Form 3: api.extend(auth=auth, storage=storage, ...)
        for k, v in kwargs.items():
            self._register_plugin(k, v)

        return self

    def _register_plugin(self, name: str, provider: Any) -> None:
        name_clean = name.lower().strip()
        self.plugins.register(name_clean, provider)
        default_registry.register(name_clean, provider)

        # Built-in direct attributes on Api instance
        if name_clean == "auth":
            self.configure_auth(provider)
        elif name_clean == "storage":
            self.storage = provider
        elif name_clean in ("tasks", "celery"):
            self.tasks = provider
        elif name_clean == "catalog":
            self.catalog = provider
        elif name_clean == "payment":
            self.payment = provider
        else:
            setattr(self, name_clean, provider)

    def get_plugin(self, name: str) -> Optional[Any]:
        return self.plugins.get(name) or default_registry.get(name)

    def configure_auth(self, auth_instance: Any) -> None:
        """Attach authentication manager (ApiAuth or flask-user-auth)."""
        if isinstance(auth_instance, ApiAuth):
            self.auth = auth_instance
        else:
            self.auth = ApiAuth(backend=auth_instance)
        self.plugins.register("auth", auth_instance)
        default_registry.register("auth", auth_instance)

    def create_schema(
        self,
        model: Any,
        fields: Optional[Any] = None,
        exclude: Optional[Any] = None,
        load_only: Optional[Any] = None,
        dump_only: Optional[Any] = None,
        relationships: Optional[Dict[str, Any]] = None,
        depth: int = 0,
        recurse: Optional[Any] = None,
        custom_fields: Optional[Dict[str, Any]] = None,
        schema_name: Optional[str] = None,
    ) -> Type[SchemaMixin]:
        """Auto-generate a Marshmallow schema from a SQLAlchemy model."""
        return create_schema(
            model=model,
            fields=fields,
            exclude=exclude,
            load_only=load_only,
            dump_only=dump_only,
            relationships=relationships,
            depth=depth,
            recurse=recurse,
            custom_fields=custom_fields,
            schema_name=schema_name,
        )

    def create_resource(
        self,
        model: Any,
        schema: Optional[Any] = None,
        name: str = "",
        pk_field: str = "id",
        pk_type: str = "int",
        fallback_pk: Optional[str] = None,
        fallback_type: str = "string",
        allowed_lookup_fields: Optional[List[str]] = None,
        methods: Union[List[str], Tuple[str, ...]] = (
            "list",
            "retrieve",
            "create",
            "update",
            "partial_update",
            "delete",
            "bulk_create",
            "bulk_delete",
        ),
        paginate: bool = True,
        schema_overrides: Optional[Dict[str, Any]] = None,
        filter_fields: Optional[List[str]] = None,
        search_fields: Optional[List[str]] = None,
        sort_fields: Optional[List[str]] = None,
        default_sort_field: Optional[str] = None,
        auth_required: bool = False,
        auth_actions: Optional[Dict[str, Any]] = None,
        singleton: bool = False,
        singleton_pk: Optional[Any] = None,
        resolve_singleton: Optional[Callable] = None,
        envelope_key: Optional[str] = None,
        data_envelope_key: Optional[str] = None,
        items_key: Optional[str] = None,
        url_prefix: Optional[str] = None,
        **kwargs
    ) -> ApiMixin:
        """Declarative resource factory creating and registering an ApiMixin instance."""
        resource_name = name or (model.__tablename__ if hasattr(model, "__tablename__") else model.__name__.lower())
        resolved_schema = schema if schema is not None else self.create_schema(model)

        resolved_env_key = data_envelope_key if data_envelope_key is not None else (envelope_key if envelope_key is not None else self.envelope_key)
        attrs = {
            "model": model,
            "schema": resolved_schema,
            "name": resource_name,
            "pk_field": pk_field,
            "pk_type": pk_type,
            "fallback_pk": fallback_pk,
            "fallback_type": fallback_type,
            "allowed_lookup_fields": allowed_lookup_fields,
            "methods": methods,
            "paginate": paginate,
            "singleton": singleton,
            "singleton_pk": singleton_pk,
            "resolve_singleton": resolve_singleton,
            "schema_overrides": schema_overrides or {},
            "filter_fields": filter_fields or [],
            "search_fields": search_fields or [],
            "sort_fields": sort_fields or [],
            "default_sort_field": default_sort_field,
            "auth_required": auth_required,
            "auth_actions": auth_actions or {},
            "envelope_key": resolved_env_key,
            "data_envelope_key": resolved_env_key,
            "items_key": items_key or self.items_key,
            **kwargs,
        }

        for k, v in list(attrs.items()):
            if callable(v) and not inspect.isclass(v):
                try:
                    params = list(inspect.signature(v).parameters.keys())
                    if not (params and params[0] == "self"):
                        attrs[k] = staticmethod(v)
                except (ValueError, TypeError):
                    pass

        cls_name = f"{resource_name.capitalize()}ApiResource"
        resource_cls = type(cls_name, (ApiMixin,), attrs)
        return self.register_resource(resource_cls, url_prefix=url_prefix)

    def create_nested_resource(
        self,
        parent: Union[str, ApiMixin, Type[ApiMixin]],
        model: Any,
        schema: Optional[Any] = None,
        name: str = "",
        parent_fk_field: Optional[str] = None,
        parent_url_param: Optional[str] = None,
        resolve_parent: Optional[Union[Callable, Dict[str, Callable]]] = None,
        *,
        parent_pk_field: Optional[str] = None,
        parent_param: Optional[str] = None,
        resolve_parent_pk: Optional[Union[Callable, Dict[str, Callable]]] = None,
        **kwargs
    ) -> ApiMixin:
        """Mount a nested child sub-resource under a parent resource.

        Parameters
        ----------
        parent:
            The parent resource instance, class, or registered resource name.
        model:
            The child SQLAlchemy model.
        parent_fk_field:
            Foreign-key column on the CHILD model that references the parent
            (e.g. ``"user_id"``). Auto-detected from the child's foreign keys
            when omitted.
        parent_url_param:
            Name of the URL variable carrying the parent identifier.
            Defaults to ``parent_fk_field`` when known, otherwise
            ``"<parent_name>_id"``.
        resolve_parent:
            Optional callable or dict of callables used to resolve the parent
            instance from the URL identifier. Defaults to the parent
            resource's own lookup.

        Example:
            api.create_nested_resource(
                parent=user_api,
                model=Order,
                schema=OrderSchema,
                name="orders",
                parent_fk_field="user_id"
            )
            # Mounts /users/<user_id>/orders and /users/<user_id>/orders/<order_id>

        The previous names ``parent_pk_field``, ``parent_param`` and
        ``resolve_parent_pk`` remain supported as deprecated aliases.
        """
        parent_fk_field = resolve_deprecated_alias(
            "parent_fk_field", parent_fk_field, "parent_pk_field", parent_pk_field
        )
        parent_url_param = resolve_deprecated_alias(
            "parent_url_param", parent_url_param, "parent_param", parent_param
        )
        resolve_parent = resolve_deprecated_alias(
            "resolve_parent", resolve_parent, "resolve_parent_pk", resolve_parent_pk
        )

        parent_res = self.resources.get(parent) if isinstance(parent, str) else parent
        if parent_res is None:
            raise ValueError(f"Parent resource '{parent}' not found or not yet registered.")

        parent_name = parent_res.name
        resource_name = name or (model.__tablename__ if hasattr(model, "__tablename__") else model.__name__.lower())

        # Auto-detect parent foreign key on child model if not explicitly given
        resolved_fk = parent_fk_field
        if not resolved_fk and hasattr(model, "__table__") and hasattr(parent_res.model, "__tablename__"):
            parent_table = parent_res.model.__tablename__
            for col in model.__table__.columns:
                for fk in col.foreign_keys:
                    if fk.column.table.name == parent_table:
                        resolved_fk = col.name
                        break
                if resolved_fk:
                    break

        parent_url_param_name = parent_url_param or resolved_fk or f"{parent_name}_id"

        parent_converter = "string" if (parent_res.fallback_pk or parent_res.pk_type == "string") else parent_res.pk_type
        parent_base = f"{self.prefix}/{parent_name}" if self.prefix else f"/{parent_name}"
        nested_prefix = f"{parent_base}/<{parent_converter}:{parent_url_param_name}>/{resource_name}"

        return self.create_resource(
            model=model,
            schema=schema,
            name=f"{parent_name}_{resource_name}",
            url_prefix=nested_prefix,
            is_nested=True,
            parent_resource=parent_res,
            parent_fk_field=resolved_fk or f"{parent_name}_id",
            parent_url_param=parent_url_param_name,
            resolve_parent=resolve_parent,
            **kwargs,
        )

    def register_resource(
        self,
        resource_or_cls: Union[Type[ApiMixin], ApiMixin],
        url_prefix: Optional[str] = None,
    ) -> ApiMixin:
        """Register a class-based or instance-based resource with URL endpoints."""
        if inspect.isclass(resource_or_cls):
            instance = resource_or_cls(
                envelope_key=getattr(resource_or_cls, "envelope_key", self.envelope_key),
                items_key=getattr(resource_or_cls, "items_key", self.items_key),
            )
        else:
            instance = resource_or_cls

        instance.api = self
        instance._normalize_nested_aliases()
        name = instance.name or (
            instance.model.__tablename__
            if hasattr(instance.model, "__tablename__")
            else instance.__class__.__name__.lower().replace("api", "")
        )
        instance.name = name

        if not url_prefix:
            url_prefix = f"{self.prefix}/{name}" if self.prefix else f"/{name}"
        url_prefix = url_prefix.rstrip("/")

        self.resources[name] = instance

        raw_methods = [normalize_action(m) for m in instance.methods]
        supported_actions = set(raw_methods)
        if "get" in instance.methods:
            supported_actions.add("list")
            supported_actions.add("retrieve")
        if "post" in instance.methods:
            supported_actions.add("create")
        if "put" in instance.methods:
            supported_actions.add("update")
        if "patch" in instance.methods:
            supported_actions.add("partial_update")
        if "delete" in instance.methods:
            supported_actions.add("delete")

        id_converter = "string" if (instance.fallback_pk or instance.pk_type == "string") else instance.pk_type
        id_route = f"{url_prefix}/<{id_converter}:identifier>"

        if instance.singleton:
            action_routes = [
                ("retrieve", ["GET"], url_prefix, instance.retrieve),
                ("create", ["POST"], url_prefix, instance.create),
                ("update", ["PUT"], url_prefix, instance.update),
                ("partial_update", ["PATCH"], url_prefix, instance.partial_update),
                ("delete", ["DELETE"], url_prefix, instance.delete),
            ]
        else:
            action_routes = [
                ("list", ["GET"], url_prefix, instance.list),
                ("create", ["POST"], url_prefix, instance.create),
                ("retrieve", ["GET"], id_route, instance.retrieve),
                ("update", ["PUT"], id_route, instance.update),
                ("partial_update", ["PATCH"], id_route, instance.partial_update),
                ("delete", ["DELETE"], id_route, instance.delete),
                ("bulk_create", ["POST"], f"{url_prefix}/bulk", instance.bulk_create),
                ("bulk_delete", ["POST"], f"{url_prefix}/bulk-delete", instance.bulk_delete),
            ]

        for action_name, http_methods, rule, handler in action_routes:
            if action_name in supported_actions:
                endpoint = f"{name}_{action_name}"
                view_func = self._build_view(instance, action_name, handler)
                self._add_url_rule(rule, endpoint, view_func, methods=http_methods)

        # Register custom @action / @route methods
        for attr_name in dir(instance):
            attr = getattr(instance, attr_name)
            if callable(attr) and getattr(attr, "_is_action", False):
                act_methods = getattr(attr, "_action_methods", ["GET"])
                act_detail = getattr(attr, "_action_detail", False)
                act_url_path = getattr(attr, "_action_url_path", attr_name).strip("/")
                act_schema = getattr(attr, "_action_schema", None)
                act_res_schema = getattr(attr, "_action_response_schema", None)
                act_validate = getattr(attr, "_action_validate_schema", True)

                # Schema resolution (req_schema and res_schema)
                req_schema_cls, res_schema_cls = None, None
                if act_res_schema is not None:
                    req_schema_cls = act_schema
                    res_schema_cls = act_res_schema
                elif act_schema is not None:
                    if isinstance(act_schema, (list, tuple)) and len(act_schema) == 2:
                        req_schema_cls, res_schema_cls = act_schema[0], act_schema[1]
                    elif isinstance(act_schema, dict):
                        req_schema_cls = (
                            act_schema.get("req")
                            or act_schema.get("request")
                            or act_schema.get("load")
                        )
                        res_schema_cls = (
                            act_schema.get("res")
                            or act_schema.get("response")
                            or act_schema.get("dump")
                        )
                    else:
                        if any(m in ("POST", "PUT", "PATCH") for m in act_methods):
                            req_schema_cls = act_schema
                            res_schema_cls = None
                        else:
                            req_schema_cls = None
                            res_schema_cls = act_schema

                req_schema = req_schema_cls() if isinstance(req_schema_cls, type) else req_schema_cls
                res_schema = res_schema_cls() if isinstance(res_schema_cls, type) else res_schema_cls

                if instance.singleton:
                    rule = f"{url_prefix}/{act_url_path}"
                else:
                    rule = f"{id_route}/{act_url_path}" if act_detail else f"{url_prefix}/{act_url_path}"
                endpoint = f"{name}_{attr_name}"

                if act_detail:
                    def make_detail_handler(
                        action_func=attr,
                        cur_req_schema=req_schema,
                        cur_res_schema=res_schema,
                        cur_act_validate=act_validate,
                    ):
                        sig = inspect.signature(action_func)
                        accepts_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())

                        def detail_handler(identifier=None, **kw):
                            parent_id = kw.get("parent_id") or (kw.get(instance.parent_url_param) if instance.is_nested else None)
                            parent = instance.get_parent_object(parent_id) if instance.is_nested else None

                            # Load & validate data
                            raw_data = None
                            if request.is_json or request.method in ("POST", "PUT", "PATCH"):
                                raw_data = request.get_json(silent=True)
                                if raw_data is None and request.form:
                                    raw_data = request.form.to_dict()
                            if raw_data is None and request.method in ("POST", "PUT", "PATCH"):
                                raw_data = {}

                            if cur_act_validate and cur_req_schema is not None and raw_data is not None:
                                try:
                                    validated_data = cur_req_schema.load(raw_data)
                                except ValidationError as err:
                                    raise ValidationErrorResponse(err.messages)
                            else:
                                validated_data = raw_data

                            ctx = instance.make_context(
                                action=action_func.__name__,
                                identifier=identifier,
                                parent_id=parent_id,
                                parent=parent,
                                data=validated_data,
                                validated_data=validated_data,
                            )
                            obj = instance.get_object(identifier, action=action_func.__name__, parent_id=parent_id, parent=parent, ctx=ctx)
                            ctx.instance = obj

                            call_kwargs = {}
                            for param_name in sig.parameters:
                                if param_name in ("identifier", "obj", "instance"):
                                    continue
                                if param_name in ("ctx", "context"):
                                    call_kwargs[param_name] = ctx
                                elif param_name in ("data", "payload", "body", "validated_data"):
                                    call_kwargs[param_name] = validated_data
                                elif param_name == "parent_id" and parent_id is not None:
                                    call_kwargs["parent_id"] = parent_id
                                elif param_name == "parent" and parent is not None:
                                    call_kwargs["parent"] = parent
                                elif param_name in kw:
                                    call_kwargs[param_name] = kw[param_name]
                            if accepts_kwargs:
                                for k, v in kw.items():
                                    if k not in call_kwargs:
                                        call_kwargs[k] = v
                                if parent is not None and "parent" not in call_kwargs:
                                    call_kwargs["parent"] = parent
                                if "ctx" not in call_kwargs:
                                    call_kwargs["ctx"] = ctx
                                if validated_data is not None and not any(p in call_kwargs for p in ("data", "payload", "body", "validated_data")):
                                    call_kwargs["data"] = validated_data

                            res = action_func(obj, **call_kwargs)

                            # Format response
                            if isinstance(res, Response):
                                return res
                            if isinstance(res, tuple):
                                payload = res[0]
                                status = res[1]
                                headers = res[2] if len(res) > 2 else {}
                                if isinstance(payload, Response):
                                    return res
                                if cur_res_schema is not None and hasattr(cur_res_schema, "dump") and payload is not None and not isinstance(payload, (str, bytes)):
                                    try:
                                        many = isinstance(payload, list)
                                        payload = cur_res_schema.dump(payload, many=many)
                                    except Exception:
                                        pass
                                return jsonify(instance.envelope(payload)), status, headers
                            else:
                                if cur_res_schema is not None and hasattr(cur_res_schema, "dump") and res is not None and not isinstance(res, (str, bytes)):
                                    try:
                                        many = isinstance(res, list)
                                        res = cur_res_schema.dump(res, many=many)
                                    except Exception:
                                        pass
                                return jsonify(instance.envelope(res))

                        return detail_handler
                    h = make_detail_handler()
                else:
                    def make_collection_handler(
                        action_func=attr,
                        cur_req_schema=req_schema,
                        cur_res_schema=res_schema,
                        cur_act_validate=act_validate,
                    ):
                        sig = inspect.signature(action_func)
                        accepts_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())

                        def collection_handler(**kw):
                            parent_id = kw.get("parent_id") or (kw.get(instance.parent_url_param) if instance.is_nested else None)
                            parent = instance.get_parent_object(parent_id) if instance.is_nested else None

                            # Load & validate data
                            raw_data = None
                            if request.is_json or request.method in ("POST", "PUT", "PATCH"):
                                raw_data = request.get_json(silent=True)
                                if raw_data is None and request.form:
                                    raw_data = request.form.to_dict()
                            if raw_data is None and request.method in ("POST", "PUT", "PATCH"):
                                raw_data = {}

                            if cur_act_validate and cur_req_schema is not None and raw_data is not None:
                                try:
                                    validated_data = cur_req_schema.load(raw_data)
                                except ValidationError as err:
                                    raise ValidationErrorResponse(err.messages)
                            else:
                                validated_data = raw_data

                            ctx = instance.make_context(
                                action=action_func.__name__,
                                parent_id=parent_id,
                                parent=parent,
                                data=validated_data,
                                validated_data=validated_data,
                            )

                            call_kwargs = {}
                            for param_name in sig.parameters:
                                if param_name in ("ctx", "context"):
                                    call_kwargs[param_name] = ctx
                                elif param_name in ("data", "payload", "body", "validated_data"):
                                    call_kwargs[param_name] = validated_data
                                elif param_name == "parent_id" and parent_id is not None:
                                    call_kwargs["parent_id"] = parent_id
                                elif param_name == "parent" and parent is not None:
                                    call_kwargs["parent"] = parent
                                elif param_name in kw:
                                    call_kwargs[param_name] = kw[param_name]
                            if accepts_kwargs:
                                for k, v in kw.items():
                                    if k not in call_kwargs:
                                        call_kwargs[k] = v
                                if parent is not None and "parent" not in call_kwargs:
                                    call_kwargs["parent"] = parent
                                if "ctx" not in call_kwargs:
                                    call_kwargs["ctx"] = ctx
                                if validated_data is not None and not any(p in call_kwargs for p in ("data", "payload", "body", "validated_data")):
                                    call_kwargs["data"] = validated_data

                            res = action_func(**call_kwargs)

                            # Format response
                            if isinstance(res, Response):
                                return res
                            if isinstance(res, tuple):
                                payload = res[0]
                                status = res[1]
                                headers = res[2] if len(res) > 2 else {}
                                if isinstance(payload, Response):
                                    return res
                                if cur_res_schema is not None and hasattr(cur_res_schema, "dump") and payload is not None and not isinstance(payload, (str, bytes)):
                                    try:
                                        many = isinstance(payload, list)
                                        payload = cur_res_schema.dump(payload, many=many)
                                    except Exception:
                                        pass
                                return jsonify(instance.envelope(payload)), status, headers
                            else:
                                if cur_res_schema is not None and hasattr(cur_res_schema, "dump") and res is not None and not isinstance(res, (str, bytes)):
                                    try:
                                        many = isinstance(res, list)
                                        res = cur_res_schema.dump(res, many=many)
                                    except Exception:
                                        pass
                                return jsonify(instance.envelope(res))

                        return collection_handler
                    h = make_collection_handler()

                view_func = self._build_view(instance, attr_name, h)
                self._add_url_rule(rule, endpoint, view_func, methods=act_methods)

        return instance

    def _build_view(self, instance: ApiMixin, action_name: str, handler: Callable) -> Callable:
        """Wrap view function with decorators, auth checking, nested parameter routing, and error handling."""
        @wraps(handler)
        def view_wrapper(*args, **kwargs):
            req_id = request.headers.get("X-Request-ID")
            try:
                # If nested, pass parent_id to handler
                if instance.is_nested and instance.parent_url_param in kwargs:
                    parent_id = kwargs.pop(instance.parent_url_param)
                    kwargs["parent_id"] = parent_id

                # Auth enforcement if auth_required or action in auth_actions
                auth_req = instance.auth_required or (instance.auth_actions and instance.auth_actions.get(action_name, False))
                if auth_req and self.auth:
                    auth_action_setting = instance.auth_actions.get(action_name) if instance.auth_actions else None
                    if auth_action_setting == "admin":
                        auth_decorator = self.auth.admin_required()
                    else:
                        auth_decorator = self.auth.requires_auth()
                    return auth_decorator(lambda: handler(*args, **kwargs))()

                return handler(*args, **kwargs)
            except ApiError as err:
                r_id = req_id or (getattr(instance.ctx, "request_id", None) if instance.ctx else None)
                return format_error_response(
                    message=err.message,
                    code=err.code,
                    details=err.details,
                    status_code=err.status_code,
                    headers=err.headers,
                    request_id=r_id,
                )
            except HTTPException as err:
                r_id = req_id or (getattr(instance.ctx, "request_id", None) if instance.ctx else None)
                return format_error_response(
                    message=err.description or "Request failed",
                    code="http_error",
                    status_code=err.code or 400,
                    request_id=r_id,
                )
            except Exception as err:
                r_id = req_id or (getattr(instance.ctx, "request_id", None) if instance.ctx else None)
                current_app.logger.exception(f"Unhandled exception in API view {action_name}")
                return format_error_response(
                    message=str(err) if current_app.debug else "Internal server error",
                    code="internal_error",
                    status_code=500,
                    request_id=r_id,
                )

        decorators = list(instance.decorators)

        auth_action_setting = instance.auth_actions.get(action_name)
        if (instance.auth_required and auth_action_setting is not False) or auth_action_setting:
            if self.auth:
                if auth_action_setting == "admin":
                    decorators.append(self.auth.admin_required())
                else:
                    decorators.append(self.auth.requires_auth())

        action_decorators = instance.method_decorators.get(action_name, [])
        decorators.extend(action_decorators)

        final_view = view_wrapper
        for dec in reversed(decorators):
            final_view = dec(final_view)

        return final_view

    def _add_url_rule(self, rule: str, endpoint: str, view_func: Callable, methods: List[str]) -> None:
        if self.target is None:
            raise RuntimeError("Api instance is not attached to a Flask app or Blueprint.")
        self.target.add_url_rule(rule, endpoint, view_func, methods=methods)

    def mount_docs(
        self,
        url_prefix: Optional[str] = None,
        title: Optional[str] = None,
        version: Optional[str] = None,
        description: Optional[str] = None,
    ) -> None:
        """Mount OpenAPI JSON and Swagger UI endpoints."""
        from .docs import mount_swagger_docs
        mount_swagger_docs(
            self,
            url_prefix=url_prefix or self.docs_url,
            title=title or self.docs_title,
            version=version,
            description=description,
        )
