from typing import Any, Dict, List, Optional, Union
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


def _resolve_app_config(api: Any) -> Dict[str, Any]:
    """Extract configuration dictionary from api.target or Flask's current_app."""
    if hasattr(api, "target") and api.target is not None:
        if hasattr(api.target, "config"):
            return dict(api.target.config)
        if hasattr(api.target, "app") and hasattr(api.target.app, "config"):
            return dict(api.target.app.config)
    try:
        from flask import current_app
        if current_app:
            return dict(current_app.config)
    except (RuntimeError, AttributeError):
        pass
    return {}


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


def generate_openapi_spec(
    api: Any,
    title: Optional[str] = None,
    version: Optional[str] = None,
    description: Optional[str] = None,
    servers: Optional[Any] = None,
    license_info: Optional[Union[str, Dict[str, Any]]] = None,
    api_key: Optional[Union[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Build OpenAPI 3.0.0 specification from registered Api resources and Flask config.

    Supports configuration via app.config:
    - API_TITLE / OPENAPI_TITLE: info.title
    - API_VERSION / OPENAPI_VERSION: info.version
    - API_DESCRIPTION / OPENAPI_DESCRIPTION: info.description
    - API_URL / API_SERVERS: servers list
    - API_LICENSE / OPENAPI_LICENSE: info.license
    - API_KEY / API_KEY_NAME: components.securitySchemes.apiKeyAuth
    - API_TERMS_OF_SERVICE: info.termsOfService
    - API_CONTACT: info.contact
    """
    cfg = _resolve_app_config(api)

    resolved_title = title or cfg.get("API_TITLE") or cfg.get("OPENAPI_TITLE") or "API Documentation"
    resolved_version = version or cfg.get("API_VERSION") or cfg.get("OPENAPI_VERSION") or "1.0.0"
    resolved_description = (
        description
        if description is not None
        else (cfg.get("API_DESCRIPTION") or cfg.get("OPENAPI_DESCRIPTION") or "")
    )
    resolved_servers = servers if servers is not None else (cfg.get("API_SERVERS") or cfg.get("API_URL"))
    resolved_license = license_info if license_info is not None else (cfg.get("API_LICENSE") or cfg.get("OPENAPI_LICENSE"))
    resolved_api_key = api_key if api_key is not None else (cfg.get("API_KEY") or cfg.get("API_KEY_NAME"))
    terms_of_service = cfg.get("API_TERMS_OF_SERVICE")
    contact = cfg.get("API_CONTACT")

    info: Dict[str, Any] = {
        "title": resolved_title,
        "version": resolved_version,
    }
    if resolved_description:
        info["description"] = resolved_description
    if terms_of_service:
        info["termsOfService"] = terms_of_service
    if contact:
        info["contact"] = contact if isinstance(contact, dict) else {"name": str(contact)}
    if resolved_license:
        if isinstance(resolved_license, str):
            info["license"] = {"name": resolved_license}
        elif isinstance(resolved_license, dict):
            info["license"] = resolved_license

    spec: Dict[str, Any] = {
        "openapi": "3.0.0",
        "info": info,
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

    # Servers list from API_URL / API_SERVERS
    if resolved_servers:
        servers_list: List[Dict[str, Any]] = []
        if isinstance(resolved_servers, str):
            servers_list.append({"url": resolved_servers})
        elif isinstance(resolved_servers, list):
            for s in resolved_servers:
                servers_list.append(s if isinstance(s, dict) else {"url": str(s)})
        elif isinstance(resolved_servers, dict):
            servers_list.append(resolved_servers)
        if servers_list:
            spec["servers"] = servers_list

    # Security schemes: API Key
    has_api_key = bool(resolved_api_key)
    if resolved_api_key:
        key_name = (
            resolved_api_key.get("name")
            if isinstance(resolved_api_key, dict)
            else (cfg.get("API_KEY_NAME") or (resolved_api_key if isinstance(resolved_api_key, str) and not resolved_api_key.startswith("http") else "X-API-Key"))
        )
        key_in = (
            resolved_api_key.get("in", "header")
            if isinstance(resolved_api_key, dict)
            else (cfg.get("API_KEY_IN") or "header")
        )
        key_desc = (
            resolved_api_key.get("description", "API Key Authentication")
            if isinstance(resolved_api_key, dict)
            else "API Key Authentication"
        )
        spec["components"]["securitySchemes"]["apiKeyAuth"] = {
            "type": "apiKey",
            "name": key_name,
            "in": key_in,
            "description": key_desc,
        }

    # Security schemes: Bearer JWT from api.auth
    has_bearer = api.auth is not None
    if has_bearer:
        spec["components"]["securitySchemes"]["bearerAuth"] = {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
        }

    # Construct security requirement for operations
    default_security: List[Dict[str, List[Any]]] = []
    if has_bearer:
        default_security.append({"bearerAuth": []})
    if has_api_key:
        default_security.append({"apiKeyAuth": []})

    for name, resource in api.resources.items():
        base_path = f"{api.prefix}/{name}" if api.prefix else f"/{name}"
        res_errors = getattr(resource, "error_responses", {}) or {}
        has_auth = bool(resource.auth_required and (has_bearer or has_api_key))

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
                if has_auth and default_security:
                    for op in paths.values():
                        op.setdefault("security", default_security)
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
            if has_auth and default_security:
                for op in coll_paths.values():
                    op.setdefault("security", default_security)
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
                "responses": _format_op_responses(200, f"Deleted {name} record", needs_auth=has_auth, has_lookup=True, custom_errors=res_errors),
            }

        if detail_paths:
            if has_auth and default_security:
                for op in detail_paths.values():
                    op.setdefault("security", default_security)
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
                    if has_auth and default_security:
                        op.setdefault("security", default_security)
                    spec["paths"][act_route][m_lower] = op

    return spec


def mount_swagger_docs(
    api: Any,
    url_prefix: Optional[str] = None,
    title: Optional[str] = None,
    version: Optional[str] = None,
    description: Optional[str] = None,
) -> None:
    """Mount Swagger UI and OpenAPI JSON endpoint on the target app or blueprint."""
    cfg = _resolve_app_config(api)

    configured_url = cfg.get("API_DOCS_URL")
    if not configured_url and cfg.get("API_DOCS") and isinstance(cfg.get("API_DOCS"), str):
        configured_url = cfg.get("API_DOCS")

    prefix = url_prefix or configured_url or "/docs"
    resolved_title = title or cfg.get("API_TITLE") or cfg.get("OPENAPI_TITLE") or "API Documentation"

    json_route = f"{prefix.rstrip('/')}/openapi.json"
    ui_route = prefix.rstrip("/")

    def openapi_json_view():
        return jsonify(generate_openapi_spec(
            api,
            title=resolved_title,
            version=version,
            description=description,
        ))

    def swagger_ui_view():
        spec_url = json_route
        return render_template_string(SWAGGER_HTML_TEMPLATE, title=resolved_title, spec_url=spec_url)

    api._add_url_rule(json_route, "openapi_json", openapi_json_view, methods=["GET"])
    api._add_url_rule(ui_route, "swagger_ui", swagger_ui_view, methods=["GET"])
