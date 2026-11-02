from typing import Any, Dict, Optional, Union
from flask import jsonify, render_template_string


SWAGGER_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>{{ title }}</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css">
</head>
<body style="margin: 0; background: #fafafa;">
  <div id="swagger-ui"></div>
  <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
  <script>
    window.onload = function() {
      SwaggerUIBundle({
        url: "{{ spec_url }}",
        dom_id: '#swagger-ui',
        deepLinking: true,
        presets: [
          SwaggerUIBundle.presets.apis,
          SwaggerUIBundle.SwaggerUIStandalonePreset
        ],
        layout: "BaseLayout"
      });
    };
  </script>
</body>
</html>
"""


def _format_op_responses(
    success_status: int,
    success_description: str,
    needs_auth: bool = False,
    is_mutation: bool = False,
    has_lookup: bool = False,
    custom_errors: Optional[Dict[Union[int, str], Any]] = None,
) -> Dict[str, Any]:
    """Assemble operation responses including standardized error responses."""
    responses: Dict[str, Any] = {
        str(success_status): {"description": success_description}
    }
    if is_mutation:
        responses["400"] = {"$ref": "#/components/responses/400BadRequest"}
        responses["422"] = {"$ref": "#/components/responses/422ValidationError"}
    if has_lookup:
        responses["404"] = {"$ref": "#/components/responses/404NotFound"}
    if needs_auth:
        responses["401"] = {"$ref": "#/components/responses/401Unauthorized"}
        responses["403"] = {"$ref": "#/components/responses/403Forbidden"}

    if custom_errors:
        for status_code, val in custom_errors.items():
            sc_str = str(status_code)
            if isinstance(val, dict):
                responses[sc_str] = val
            elif isinstance(val, str):
                responses[sc_str] = {
                    "description": val,
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}},
                }

    return responses


def generate_openapi_spec(api: Any, title: str = "API Documentation", version: str = "1.0.0") -> Dict[str, Any]:
    """Build OpenAPI 3.0.0 specification from registered Api resources with standardized error_responses."""
    spec: Dict[str, Any] = {
        "openapi": "3.0.0",
        "info": {
            "title": title,
            "version": version,
        },
        "paths": {},
        "components": {
            "schemas": {
                "ErrorDetail": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string", "example": "validation_error"},
                        "message": {"type": "string", "example": "Validation error"},
                        "details": {"type": "object"},
                        "request_id": {"type": "string", "example": "03e48816-43b6-455b-b5cb-0fe4ecaa84b6"},
                    },
                    "required": ["code", "message"],
                },
                "ErrorResponse": {
                    "type": "object",
                    "properties": {
                        "success": {"type": "boolean", "example": False},
                        "error": {"$ref": "#/components/schemas/ErrorDetail"},
                        "errors": {"type": "object"},
                    },
                    "required": ["success", "error"],
                },
            },
            "responses": {
                "400BadRequest": {
                    "description": "Bad Request / Malformed Syntax",
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}},
                },
                "401Unauthorized": {
                    "description": "Authentication required or invalid Bearer token",
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}},
                },
                "403Forbidden": {
                    "description": "Permission denied / Administrator access required",
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}},
                },
                "404NotFound": {
                    "description": "Resource not found",
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}},
                },
                "412PreconditionFailed": {
                    "description": "Precondition failed (ETag mismatch)",
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}},
                },
                "422ValidationError": {
                    "description": "Validation error (invalid field values or missing required fields)",
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}},
                },
                "500InternalError": {
                    "description": "Internal server error",
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}},
                },
            },
            "securitySchemes": {},
        },
    }

    if api.auth is not None:
        spec["components"]["securitySchemes"]["bearerAuth"] = {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
        }

    for name, resource in api.resources.items():
        base_path = f"{api.prefix}/{name}" if api.prefix else f"/{name}"
        res_errors = getattr(resource, "error_responses", {}) or {}
        has_auth = bool(resource.auth_required and api.auth)

        # Singleton resources have no {identifier} in their URLs
        if resource.singleton:
            paths: Dict[str, Any] = {}
            if "retrieve" in resource.methods or "get" in resource.methods:
                paths["get"] = {
                    "tags": [name.capitalize()],
                    "summary": f"Retrieve singleton {name}",
                    "responses": _format_op_responses(200, f"{name} record", needs_auth=has_auth, has_lookup=True, custom_errors=res_errors),
                }
            if "create" in resource.methods or "post" in resource.methods:
                singleton_create_errors = dict(res_errors)
                singleton_create_errors.setdefault(409, "Conflict - record already exists")
                paths["post"] = {
                    "tags": [name.capitalize()],
                    "summary": f"Create singleton {name}",
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"type": "object"}}},
                    },
                    "responses": _format_op_responses(201, f"Created {name} record", needs_auth=has_auth, is_mutation=True, custom_errors=singleton_create_errors),
                }
            if "update" in resource.methods or "put" in resource.methods:
                paths["put"] = {
                    "tags": [name.capitalize()],
                    "summary": f"Update singleton {name}",
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"type": "object"}}},
                    },
                    "responses": _format_op_responses(200, f"Updated {name} record", needs_auth=has_auth, is_mutation=True, custom_errors=res_errors),
                }
            if "partial_update" in resource.methods or "patch" in resource.methods:
                paths["patch"] = {
                    "tags": [name.capitalize()],
                    "summary": f"Partially update singleton {name}",
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"type": "object"}}},
                    },
                    "responses": _format_op_responses(200, f"Updated {name} record", needs_auth=has_auth, is_mutation=True, custom_errors=res_errors),
                }
            if "delete" in resource.methods:
                paths["delete"] = {
                    "tags": [name.capitalize()],
                    "summary": f"Delete singleton {name}",
                    "responses": _format_op_responses(200, f"Deleted {name} record", needs_auth=has_auth, has_lookup=True, custom_errors=res_errors),
                }
            if paths:
                if has_auth:
                    for op in paths.values():
                        op.setdefault("security", [{"bearerAuth": []}])
                spec["paths"][base_path] = paths
            continue

        pk_desc = f"Unique {resource.pk_field} ({resource.pk_type})"
        if resource.fallback_pk:
            pk_desc += f" or fallback {resource.fallback_pk} ({resource.fallback_type})"

        item_path = f"{base_path}/{{identifier}}"

        # 1. Collection routes
        coll_paths: Dict[str, Any] = {}
        if "list" in resource.methods or "get" in resource.methods:
            params = [
                {"name": "page", "in": "query", "schema": {"type": "integer", "default": 1}},
                {"name": "per_page", "in": "query", "schema": {"type": "integer", "default": resource.per_page}},
            ]
            if resource.search_fields:
                params.append({"name": "q", "in": "query", "description": f"Search in {', '.join(resource.search_fields)}", "schema": {"type": "string"}})
            if resource.sort_fields:
                params.append({"name": "sort", "in": "query", "description": f"Sort by {', '.join(resource.sort_fields)}", "schema": {"type": "string"}})
            for f in resource.filter_fields:
                params.append({"name": f, "in": "query", "schema": {"type": "string"}})

            coll_paths["get"] = {
                "tags": [name.capitalize()],
                "summary": f"List {name}",
                "parameters": params,
                "responses": _format_op_responses(200, f"Array of {name} records", needs_auth=has_auth, custom_errors=res_errors),
            }

        if "create" in resource.methods or "post" in resource.methods:
            coll_paths["post"] = {
                "tags": [name.capitalize()],
                "summary": f"Create {name}",
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": {"type": "object"}}},
                },
                "responses": _format_op_responses(201, f"Created {name} record", needs_auth=has_auth, is_mutation=True, custom_errors=res_errors),
            }

        if coll_paths:
            if has_auth:
                for op in coll_paths.values():
                    op.setdefault("security", [{"bearerAuth": []}])
            spec["paths"][base_path] = coll_paths

        # 2. Detail routes
        detail_paths: Dict[str, Any] = {}
        id_param = {
            "name": "identifier",
            "in": "path",
            "required": True,
            "description": pk_desc,
            "schema": {"type": "string"},
        }

        if "retrieve" in resource.methods or "get" in resource.methods:
            detail_paths["get"] = {
                "tags": [name.capitalize()],
                "summary": f"Retrieve {name}",
                "parameters": [id_param],
                "responses": _format_op_responses(200, f"Single {name} record", needs_auth=has_auth, has_lookup=True, custom_errors=res_errors),
            }

        if "update" in resource.methods or "put" in resource.methods:
            detail_paths["put"] = {
                "tags": [name.capitalize()],
                "summary": f"Update {name}",
                "parameters": [id_param],
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": {"type": "object"}}},
                },
                "responses": _format_op_responses(200, f"Updated {name} record", needs_auth=has_auth, is_mutation=True, has_lookup=True, custom_errors=res_errors),
            }

        if "partial_update" in resource.methods or "patch" in resource.methods:
            detail_paths["patch"] = {
                "tags": [name.capitalize()],
                "summary": f"Partially update {name}",
                "parameters": [id_param],
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": {"type": "object"}}},
                },
                "responses": _format_op_responses(200, f"Updated {name} record", needs_auth=has_auth, is_mutation=True, has_lookup=True, custom_errors=res_errors),
            }

        if "delete" in resource.methods:
            detail_paths["delete"] = {
                "tags": [name.capitalize()],
                "summary": f"Delete {name}",
                "parameters": [id_param],
                "responses": _format_op_responses(200, "Record deleted", needs_auth=has_auth, has_lookup=True, custom_errors=res_errors),
            }

        if detail_paths:
            if has_auth:
                for op in detail_paths.values():
                    op.setdefault("security", [{"bearerAuth": []}])
            spec["paths"][item_path] = detail_paths

        # 3. Custom @action / @route endpoints
        for attr_name in dir(resource):
            attr = getattr(resource, attr_name)
            if callable(attr) and getattr(attr, "_is_action", False):
                act_methods = getattr(attr, "_action_methods", ["GET"])
                act_detail = getattr(attr, "_action_detail", False)
                act_url_path = getattr(attr, "_action_url_path", attr_name).strip("/")
                act_errors = getattr(attr, "_action_error_responses", {}) or {}
                combined_errors = {**res_errors, **act_errors}

                if resource.singleton:
                    act_route = f"{base_path}/{act_url_path}"
                else:
                    act_route = f"{item_path}/{act_url_path}" if act_detail else f"{base_path}/{act_url_path}"

                if act_route not in spec["paths"]:
                    spec["paths"][act_route] = {}

                for m in act_methods:
                    m_lower = m.lower()
                    is_mut = m_lower in ("post", "put", "patch")
                    op: Dict[str, Any] = {
                        "tags": [name.capitalize()],
                        "summary": f"{name.capitalize()} {attr_name.replace('_', ' ')}",
                        "responses": _format_op_responses(
                            200,
                            "Successful operation",
                            needs_auth=has_auth,
                            is_mutation=is_mut,
                            has_lookup=act_detail and not resource.singleton,
                            custom_errors=combined_errors,
                        ),
                    }
                    if act_detail and not resource.singleton:
                        op["parameters"] = [id_param]
                    if is_mut:
                        op["requestBody"] = {
                            "required": True,
                            "content": {"application/json": {"schema": {"type": "object"}}},
                        }
                    if has_auth:
                        op.setdefault("security", [{"bearerAuth": []}])
                    spec["paths"][act_route][m_lower] = op

    return spec


def mount_swagger_docs(api: Any, url_prefix: str = "/docs", title: str = "API Documentation") -> None:
    """Mount Swagger UI and OpenAPI JSON endpoint on the target app or blueprint."""
    json_route = f"{url_prefix.rstrip('/')}/openapi.json"
    ui_route = url_prefix.rstrip("/")

    def openapi_json_view():
        return jsonify(generate_openapi_spec(api, title=title))

    def swagger_ui_view():
        spec_url = json_route
        return render_template_string(SWAGGER_HTML_TEMPLATE, title=title, spec_url=spec_url)

    api._add_url_rule(json_route, "openapi_json", openapi_json_view, methods=["GET"])
    api._add_url_rule(ui_route, "swagger_ui", swagger_ui_view, methods=["GET"])
