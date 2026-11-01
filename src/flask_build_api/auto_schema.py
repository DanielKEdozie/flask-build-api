import inspect
from typing import Any, Dict, List, Optional, Set, Tuple, Type, Union
from marshmallow import Schema, fields
from sqlalchemy.inspection import inspect as sa_inspect
from sqlalchemy.sql import sqltypes


def _resolve_marshmallow_field(
    col: Any,
    is_dump_only: bool = False,
    is_load_only: bool = False,
) -> fields.Field:
    """Map a SQLAlchemy Column to an appropriate Marshmallow Field instance."""
    col_type = col.type

    # 1. Integers
    if isinstance(col_type, (sqltypes.Integer, sqltypes.SmallInteger, sqltypes.BigInteger)):
        field_cls = fields.Int
    # 2. Floats & Numerics
    elif isinstance(col_type, (sqltypes.Float, sqltypes.REAL)):
        field_cls = fields.Float
    elif isinstance(col_type, (sqltypes.Numeric, sqltypes.DECIMAL)):
        field_cls = fields.Float
    # 3. Booleans
    elif isinstance(col_type, sqltypes.Boolean):
        field_cls = fields.Bool
    # 4. Dates & Times
    elif isinstance(col_type, sqltypes.DateTime):
        field_cls = fields.DateTime
    elif isinstance(col_type, sqltypes.Date):
        field_cls = fields.Date
    elif isinstance(col_type, sqltypes.Time):
        field_cls = fields.Time
    # 5. Strings & Text
    elif isinstance(col_type, (sqltypes.String, sqltypes.Text, sqltypes.Unicode, sqltypes.UnicodeText)):
        field_cls = fields.Str
    # 6. JSON
    elif isinstance(col_type, sqltypes.JSON):
        field_cls = fields.Raw
    # 7. UUID
    elif hasattr(sqltypes, "Uuid") and isinstance(col_type, sqltypes.Uuid):
        field_cls = fields.UUID
    # 8. Enum
    elif isinstance(col_type, sqltypes.Enum):
        field_cls = fields.Str
    else:
        field_cls = fields.Raw

    # Requirement determination
    is_pk = getattr(col, "primary_key", False)
    has_default = getattr(col, "default", None) is not None or getattr(col, "server_default", None) is not None
    is_nullable = getattr(col, "nullable", True)

    if is_dump_only or is_pk or has_default or is_nullable:
        required = False
    else:
        required = True

    return field_cls(
        required=required,
        allow_none=is_nullable,
        dump_only=is_dump_only or is_pk,
        load_only=is_load_only,
    )


def _generate_schema_fields(
    model: Any,
    fields_list: Optional[Union[List[str], Tuple[str, ...], Set[str]]] = None,
    exclude: Optional[Union[List[str], Tuple[str, ...], Set[str]]] = None,
    load_only: Optional[Union[List[str], Tuple[str, ...], Set[str]]] = None,
    dump_only: Optional[Union[List[str], Tuple[str, ...], Set[str]]] = None,
    relationships: Optional[Dict[str, Any]] = None,
    depth: int = 0,
    custom_fields: Optional[Dict[str, Any]] = None,
    recurse: Optional[Union[bool, str, List[str], Tuple[str, ...], Set[str]]] = None,
    schema_cls: Optional[Any] = None,
    _visited: Optional[Set[Any]] = None,
) -> Dict[str, fields.Field]:
    """Inspect a SQLAlchemy model and generate Marshmallow field definitions."""
    visited = set(_visited or set())
    visited.add(model)

    try:
        mapper = sa_inspect(model)
    except Exception as e:
        raise ValueError(f"Cannot inspect model {model}: {e}")

    generated_fields: Dict[str, fields.Field] = {}
    exclude_set = set(exclude or [])
    load_only_set = set(load_only or [])
    dump_only_set = set(dump_only or [])
    include_fields = set(fields_list) if fields_list is not None and fields_list != "__all__" else None

    # 1. Map columns
    for col in mapper.columns:
        col_name = col.name
        if include_fields is not None and col_name not in include_fields:
            continue
        if col_name in exclude_set:
            continue

        is_d_only = col_name in dump_only_set or col.primary_key
        is_l_only = col_name in load_only_set
        generated_fields[col_name] = _resolve_marshmallow_field(
            col,
            is_dump_only=is_d_only,
            is_load_only=is_l_only,
        )

    # 2. Map relationships
    relationships_map = relationships or {}
    for rel in mapper.relationships:
        rel_key = rel.key

        # Check if this relationship is explicitly configured or enabled via depth
        is_configured = rel_key in relationships_map
        is_depth_allowed = depth > 0 and (include_fields is None or rel_key in include_fields)

        target_model = rel.mapper.class_
        is_self_ref = (target_model == model)

        # Check recurse configuration (for self-referential trees like Category.children)
        rel_cfg = relationships_map.get(rel_key, {})
        if isinstance(rel_cfg, (list, tuple, set)):
            rel_cfg = {"only": list(rel_cfg)}
        elif not isinstance(rel_cfg, dict):
            rel_cfg = {}

        rel_recurse = rel_cfg.get("recurse")
        if rel_recurse is None:
            if isinstance(recurse, bool):
                # When recurse=True is set, recurse on self-referential collections (like children in trees)
                rel_recurse = recurse if (is_self_ref and rel.uselist) else False
            elif isinstance(recurse, int) and not isinstance(recurse, bool):
                rel_recurse = recurse if (is_self_ref and rel.uselist) else False
            elif isinstance(recurse, (list, tuple, set)):
                rel_recurse = rel_key in recurse
            elif isinstance(recurse, str):
                rel_recurse = (rel_key == recurse)
            else:
                rel_recurse = False

        if not (is_configured or is_depth_allowed or rel_recurse):
            continue
        if rel_key in exclude_set:
            continue

        rel_only = rel_cfg.get("only")
        rel_exclude = rel_cfg.get("exclude")
        rel_write = rel_cfg.get("write", False)

        # Handle self-referential recursive nesting
        if is_self_ref and rel_recurse:
            # Check if bounded depth was requested
            recurse_depth = None
            if isinstance(rel_recurse, int) and not isinstance(rel_recurse, bool):
                recurse_depth = rel_recurse
            elif isinstance(recurse, int) and not isinstance(recurse, bool):
                recurse_depth = recurse
            elif rel_cfg.get("depth") is not None and not isinstance(rel_cfg.get("depth"), bool):
                recurse_depth = rel_cfg.get("depth")
            elif depth is not None and not isinstance(depth, bool) and depth > 0:
                recurse_depth = depth

            # Bounded recursion: nest down to recurse_depth levels
            if recurse_depth is not None and recurse_depth > 0:
                next_depth = recurse_depth - 1
                nested_schema_cls = create_schema(
                    model=target_model,
                    fields=rel_only,
                    exclude=rel_exclude,
                    depth=next_depth,
                    recurse=rel_key if next_depth > 0 else False,
                    schema_name=f"{model.__name__}_{rel_key.capitalize()}Depth{recurse_depth}AutoSchema",
                    _visited=visited,
                )
                generated_fields[rel_key] = fields.Nested(
                    nested_schema_cls,
                    many=rel.uselist,
                    dump_only=not rel_write,
                )
                continue
            elif recurse_depth == 0:
                # Reached depth boundary for bounded recursion
                continue

            # Unbounded natural recursion (depth omitted or 0)
            if schema_cls is not None:
                nested_kwargs: Dict[str, Any] = {
                    "many": rel.uselist,
                    "dump_only": not rel_write,
                }
                if rel_only:
                    nested_kwargs["only"] = tuple(rel_only)

                excludes = list(rel_exclude or [])
                # Prevent circular loop back to parent only if inverse relation is mapped as a field
                if rel.back_populates and include_fields is not None and rel.back_populates in include_fields:
                    if rel.back_populates not in excludes and rel.back_populates not in exclude_set:
                        excludes.append(rel.back_populates)

                if excludes:
                    nested_kwargs["exclude"] = tuple(excludes)

                generated_fields[rel_key] = fields.Nested(
                    lambda: schema_cls,
                    **nested_kwargs,
                )
                continue

        rel_depth = rel_cfg.get("depth", depth - 1)

        # Prevent circular loop if depth exhausted
        if target_model in visited and rel_depth <= 0:
            continue

        nested_schema_cls = create_schema(
            model=target_model,
            fields=rel_only,
            exclude=rel_exclude,
            depth=rel_depth,
            schema_name=f"{model.__name__}_{rel_key.capitalize()}AutoSchema",
            _visited=visited,
        )

        generated_fields[rel_key] = fields.Nested(
            nested_schema_cls,
            many=rel.uselist,
            dump_only=not rel_write,
        )

    # 3. Add custom fields
    if custom_fields:
        for f_name, f_obj in custom_fields.items():
            generated_fields[f_name] = f_obj

    return generated_fields


from marshmallow.schema import SchemaMeta


class AutoSchemaMeta(SchemaMeta):
    """Metaclass for SchemaMixin that derives fields from SQLAlchemy models."""

    @classmethod
    def get_declared_fields(
        mcs,
        klass: "AutoSchemaMeta",
        cls_fields: List[Tuple[str, fields.Field]],
        inherited_fields: List[Tuple[str, fields.Field]],
        dict_cls: type = dict,
    ) -> Dict[str, fields.Field]:
        base_fields = super().get_declared_fields(
            klass=klass,
            cls_fields=cls_fields,
            inherited_fields=inherited_fields,
            dict_cls=dict_cls,
        )

        model = getattr(klass, "model", None)
        meta_cls = getattr(klass, "Meta", None)
        if model is None and meta_cls and hasattr(meta_cls, "model"):
            model = getattr(meta_cls, "model")

        if model is not None:
            f_list = getattr(klass, "fields", None) or getattr(meta_cls, "fields", None)
            f_exclude = getattr(klass, "exclude", None) or getattr(meta_cls, "exclude", None)
            f_load_only = getattr(klass, "load_only", None) or getattr(meta_cls, "load_only", None)
            f_dump_only = getattr(klass, "dump_only", None) or getattr(meta_cls, "dump_only", None)
            f_rel = getattr(klass, "relationships", None) or getattr(meta_cls, "relationships", None)
            f_depth = getattr(klass, "depth", 0) if getattr(klass, "depth", None) is not None else getattr(meta_cls, "depth", 0)
            f_custom = getattr(klass, "custom_fields", None) or getattr(meta_cls, "custom_fields", None)
            f_recurse = getattr(klass, "recurse", None) or getattr(meta_cls, "recurse", None)

            auto_fields = _generate_schema_fields(
                model=model,
                fields_list=f_list,
                exclude=f_exclude,
                load_only=f_load_only,
                dump_only=f_dump_only,
                relationships=f_rel,
                depth=f_depth or 0,
                custom_fields=f_custom,
                recurse=f_recurse,
                schema_cls=klass,
            )

            # Insert auto-generated fields, keeping explicit fields intact
            for k, v in auto_fields.items():
                if k not in base_fields:
                    base_fields[k] = v

        return base_fields


class SchemaMixin(Schema, metaclass=AutoSchemaMeta):
    """Declarative Marshmallow Schema that automatically derives fields from a SQLAlchemy model.
    
    Usage:
        class UserSchema(SchemaMixin):
            model = User
            fields = ["id", "name", "email", "orders"]
            exclude = ["password_hash"]
            load_only = ["password"]
            dump_only = ["id", "created_at"]
            relationships = {
                "orders": {"only": ["id", "total"], "depth": 1}
            }
            depth = 1
            recurse = ["children"]  # Or True for recursive self-referential relations like Category.children
    """
    model: Any = None
    fields: Optional[Any] = None
    exclude: Optional[Any] = None
    load_only: Optional[Any] = None
    dump_only: Optional[Any] = None
    relationships: Optional[Dict[str, Any]] = None
    depth: int = 0
    recurse: Optional[Union[bool, str, List[str], Tuple[str, ...], Set[str]]] = None
    custom_fields: Optional[Dict[str, Any]] = None


def create_schema(
    model: Any,
    fields: Optional[Union[List[str], Tuple[str, ...], Set[str]]] = None,
    exclude: Optional[Union[List[str], Tuple[str, ...], Set[str]]] = None,
    load_only: Optional[Union[List[str], Tuple[str, ...], Set[str]]] = None,
    dump_only: Optional[Union[List[str], Tuple[str, ...], Set[str]]] = None,
    relationships: Optional[Dict[str, Any]] = None,
    depth: int = 0,
    recurse: Optional[Union[bool, str, List[str], Tuple[str, ...], Set[str]]] = None,
    custom_fields: Optional[Dict[str, Any]] = None,
    schema_name: Optional[str] = None,
    _visited: Optional[Set[Any]] = None,
) -> Type[SchemaMixin]:
    """Dynamically generate a Marshmallow Schema subclass from a SQLAlchemy model.
    
    Args:
        model: The SQLAlchemy model class.
        fields: Subset of fields to include (defaults to all columns).
        exclude: Fields to exclude from generation.
        load_only: Fields only available during deserialization (e.g. passwords).
        dump_only: Fields only available during serialization (e.g. IDs, timestamps).
        relationships: Configuration dictionary for related models, e.g.
                       {"category": {"only": ["id", "name"], "write": False}}
        depth: Auto-embed relationship depth (default: 0).
        recurse: Self-referential relationship name(s) or True to recurse.
        custom_fields: Additional Marshmallow field instances to inject.
        schema_name: Custom name for the generated class.
    """
    name = schema_name or f"{model.__name__}AutoSchema"
    attrs = {
        "model": model,
        "fields": fields,
        "exclude": exclude,
        "load_only": load_only,
        "dump_only": dump_only,
        "relationships": relationships,
        "depth": depth,
        "recurse": recurse,
        "custom_fields": custom_fields,
    }
    return type(name, (SchemaMixin,), attrs)
