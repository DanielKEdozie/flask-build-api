import inspect
from typing import Any, Dict, Optional, Tuple, Union
from marshmallow import Schema, ValidationError
from .errors import ValidationErrorResponse
from .utils import normalize_action


class SchemaResolver:
    """Handles resolution, caching, serialization, and deserialization of Marshmallow schemas."""

    def __init__(
        self,
        default_schema: Optional[Union[Schema, type]] = None,
        schema_overrides: Optional[Dict[str, Any]] = None,
    ):
        self.default_schema = default_schema
        self.schema_overrides = schema_overrides or {}
        self._cache: Dict[str, Schema] = {}

    def _instantiate(self, schema_or_cls: Any) -> Optional[Schema]:
        if schema_or_cls is None:
            return None
        if isinstance(schema_or_cls, type):
            return schema_or_cls()
        return schema_or_cls

    def resolve(self, action: Optional[str] = None, mode: str = "dump") -> Optional[Schema]:
        """Resolve schema instance for the given action and mode ('dump'/'res' or 'load'/'req')."""
        action = normalize_action(action) if action else "default"
        is_load = mode in ("load", "request", "req")
        cache_key = f"{action}:{mode}"

        if cache_key in self._cache:
            return self._cache[cache_key]

        spec = self.schema_overrides.get(action)
        if spec is None and action in ("update", "partial_update"):
            # Fall back across update variants
            spec = self.schema_overrides.get("update") or self.schema_overrides.get("partial_update")

        resolved = None

        if spec is not None:
            if isinstance(spec, (list, tuple)) and len(spec) == 2:
                target = spec[0] if is_load else spec[1]
                resolved = self._instantiate(target)
            elif isinstance(spec, dict):
                if is_load:
                    target = (
                        spec.get("request")
                        or spec.get("req")
                        or spec.get("load")
                        or spec.get("response")
                        or spec.get("res")
                    )
                else:
                    target = (
                        spec.get("response")
                        or spec.get("res")
                        or spec.get("dump")
                        or spec.get("request")
                        or spec.get("req")
                    )
                resolved = self._instantiate(target)
            else:
                resolved = self._instantiate(spec)

        if resolved is None and self.default_schema is not None:
            resolved = self._instantiate(self.default_schema)

        if resolved is not None:
            self._cache[cache_key] = resolved

        return resolved

    def load(
        self,
        data: Any,
        action: Optional[str] = None,
        instance: Optional[Any] = None,
        partial: bool = False,
        session: Optional[Any] = None,
    ) -> Any:
        """Validate and deserialize incoming payload with the resolved schema."""
        schema = self.resolve(action=action, mode="load")
        if schema is None:
            return data

        kwargs: Dict[str, Any] = {"partial": partial}
        load_params = inspect.signature(schema.load).parameters

        if instance is not None and ("instance" in load_params or hasattr(schema, "instance")):
            kwargs["instance"] = instance

        if session is not None and ("session" in load_params or hasattr(schema, "session")):
            kwargs["session"] = session

        try:
            return schema.load(data, **kwargs)
        except ValidationError as err:
            raise ValidationErrorResponse(details=err.messages)

    def dump(
        self,
        obj: Any,
        action: Optional[str] = None,
        many: bool = False,
        only: Optional[Tuple[str, ...]] = None,
    ) -> Any:
        """Serialize model instance or collection into JSON-serializable primitives.
        Supports sparse fieldsets via 'only' or ?fields= query param.
        """
        schema = self.resolve(action=action, mode="dump")
        if schema is None:
            return obj

        # Support sparse fieldsets (?fields=id,name,price)
        if not only:
            try:
                from flask import request
                fields_arg = request.args.get("fields")
                if fields_arg:
                    only = tuple(f.strip() for f in fields_arg.split(",") if f.strip())
            except RuntimeError:
                pass

        if only:
            try:
                # Dynamically instantiate schema clone with restricted fields
                schema = type(schema)(only=only)
            except Exception:
                pass

        return schema.dump(obj, many=many)

