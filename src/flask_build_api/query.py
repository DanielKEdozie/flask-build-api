import math
from typing import Any, Dict, List, Optional, Tuple
from flask import request
from sqlalchemy import or_, desc, asc
import sqlalchemy as sa


def apply_filters(query: Any, model: Any, filter_fields: List[str]) -> Any:
    """Apply query parameter filters to a SQLAlchemy query."""
    if not filter_fields or not hasattr(query, "filter"):
        return query

    args = request.args
    for param, value in args.items():
        if not value:
            continue

        field_name = param
        operator = "eq"
        if "__" in param:
            parts = param.split("__", 1)
            field_name, operator = parts[0], parts[1]

        if field_name not in filter_fields:
            continue

        column = getattr(model, field_name, None)
        if column is None:
            continue

        # Type conversion heuristics
        clean_val: Any = value
        if str(value).lower() in ("true", "false"):
            clean_val = str(value).lower() == "true"
        elif operator == "in":
            clean_val = [v.strip() for v in str(value).split(",") if v.strip()]
        elif operator == "null":
            clean_val = str(value).lower() in ("true", "1")

        if operator == "eq":
            query = query.filter(column == clean_val)
        elif operator == "ne":
            query = query.filter(column != clean_val)
        elif operator == "gt":
            query = query.filter(column > clean_val)
        elif operator == "gte":
            query = query.filter(column >= clean_val)
        elif operator == "lt":
            query = query.filter(column < clean_val)
        elif operator == "lte":
            query = query.filter(column <= clean_val)
        elif operator == "in":
            query = query.filter(column.in_(clean_val))
        elif operator == "like":
            query = query.filter(column.like(f"%{clean_val}%"))
        elif operator == "ilike":
            query = query.filter(column.ilike(f"%{clean_val}%"))
        elif operator == "null":
            query = query.filter(column.is_(None) if clean_val else column.isnot(None))

    return query


def apply_search(query: Any, model: Any, search_fields: List[str]) -> Any:
    """Apply full-text search across allowed fields using an OR clause."""
    search_term = request.args.get("q") or request.args.get("search")
    if not search_term or not search_fields or not hasattr(query, "filter"):
        return query

    clauses = []
    for field_name in search_fields:
        col = getattr(model, field_name, None)
        if col is not None:
            clauses.append(col.ilike(f"%{search_term}%"))

    if clauses:
        query = query.filter(or_(*clauses))

    return query


def apply_sorting(
    query: Any,
    model: Any,
    sort_fields: List[str],
    default_sort_field: Optional[str] = None
) -> Any:
    """Apply order_by to a SQLAlchemy query."""
    sort_param = request.args.get("sort") or request.args.get("order_by") or default_sort_field
    if not sort_param or not hasattr(query, "order_by"):
        return query

    sort_rules = [s.strip() for s in sort_param.split(",") if s.strip()]
    order_clauses = []

    for rule in sort_rules:
        is_desc = rule.startswith("-") or rule.endswith(":desc")
        col_name = rule.lstrip("-").replace(":desc", "").replace(":asc", "")

        if sort_fields and col_name not in sort_fields:
            continue

        column = getattr(model, col_name, None)
        if column is not None:
            order_clauses.append(desc(column) if is_desc else asc(column))

    if order_clauses:
        query = query.order_by(*order_clauses)

    return query


def paginate_query(
    query: Any,
    page: int = 1,
    per_page: int = 20,
    max_per_page: int = 100,
    allow_disable_pagination: bool = True,
    max_unpaginated_limit: Optional[int] = 1000,
) -> Tuple[List[Any], Optional[Dict[str, Any]]]:
    """Paginate a SQLAlchemy query and return (items, meta).
    Supports disabling pagination with ?paginate=false or ?all=true.
    """
    paginate_param = request.args.get("paginate") or request.args.get("pagination")
    all_param = request.args.get("all")

    is_disabled = (
        paginate_param is not None and str(paginate_param).lower() in ("false", "0", "no")
    ) or (
        all_param is not None and str(all_param).lower() in ("true", "1", "yes")
    )

    if allow_disable_pagination and is_disabled:
        if max_unpaginated_limit is not None and hasattr(query, "limit"):
            items = query.limit(max_unpaginated_limit).all()
        else:
            items = query.all() if hasattr(query, "all") else list(query)
        return items, None

    try:
        page = max(1, int(request.args.get("page", page)))
    except (ValueError, TypeError):
        page = 1

    try:
        per_page = min(max_per_page, max(1, int(request.args.get("per_page", per_page))))
    except (ValueError, TypeError):
        per_page = 20

    if hasattr(query, "paginate"):
        # Flask-SQLAlchemy legacy or pagination helper
        pagination = query.paginate(page=page, per_page=per_page, error_out=False)
        meta = {
            "page": pagination.page,
            "per_page": pagination.per_page,
            "total": pagination.total,
            "pages": pagination.pages,
            "has_next": pagination.has_next,
            "has_prev": pagination.has_prev,
        }
        return pagination.items, meta

    # Standard SQLAlchemy Query or fallback
    total = query.count() if hasattr(query, "count") else len(query)
    pages = math.ceil(total / per_page) if per_page else 0
    items = query.offset((page - 1) * per_page).limit(per_page).all() if hasattr(query, "offset") else query

    meta = {
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": pages,
        "has_next": page < pages,
        "has_prev": page > 1,
    }
    return items, meta

