from typing import Any, Dict
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


def generate_openapi_spec(api: Any, title: str = "API Documentation", version: str = "1.0.0") -> Dict[str, Any]:
    """Build OpenAPI 3.0.0 specification from registered Api resources."""
    spec: Dict[str, Any] = {
        "openapi": "3.0.0",
        "info": {
            "title": title,
            "version": version,
        },
        "paths": {},
        "components": {
            "schemas": {},
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

        # Singleton resources have no {identifier} in their URLs
        if resource.singleton:
            paths: Dict[str, Any] = {}
            if "retrieve" in resource.methods or "get" in resource.methods:
                paths["get"] = {
                    "tags": [name.capitalize()],
                    "summary": f"Retrieve singleton {name}",
                    "responses": {"200": {"description": f"{name} record"}, "404": {"description": "Not found"}},
                }
            if "create" in resource.methods or "post" in resource.methods:
                paths["post"] = {
                    "tags": [name.capitalize()],
                    "summary": f"Create singleton {name}",
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"type": "object"}}},
                    },
                    "responses": {"201": {"description": f"Created {name} record"}, "409": {"description": "Conflict - already exists"}},
                }
            if "update" in resource.methods or "put" in resource.methods:
                paths["put"] = {
                    "tags": [name.capitalize()],
                    "summary": f"Update singleton {name}",
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"type": "object"}}},
                    },
                    "responses": {"200": {"description": f"Updated {name} record"}},
                }
            if "partial_update" in resource.methods or "patch" in resource.methods:
                paths["patch"] = {
                    "tags": [name.capitalize()],
                    "summary": f"Partially update singleton {name}",
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"type": "object"}}},
                    },
                    "responses": {"200": {"description": f"Updated {name} record"}},
                }
            if "delete" in resource.methods:
                paths["delete"] = {
                    "tags": [name.capitalize()],
                    "summary": f"Delete singleton {name}",
                    "responses": {"200": {"description": f"Deleted {name} record"}},
                }
            if paths:
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
                "responses": {"200": {"description": f"Array of {name} records"}},
            }

        if "create" in resource.methods or "post" in resource.methods:
            coll_paths["post"] = {
                "tags": [name.capitalize()],
                "summary": f"Create {name}",
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": {"type": "object"}}},
                },
                "responses": {"201": {"description": f"Created {name} record"}},
            }

        if coll_paths:
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
                "responses": {"200": {"description": f"Single {name} record"}, "404": {"description": "Not found"}},
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
                "responses": {"200": {"description": f"Updated {name} record"}},
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
                "responses": {"200": {"description": f"Updated {name} record"}},
            }

        if "delete" in resource.methods:
            detail_paths["delete"] = {
                "tags": [name.capitalize()],
                "summary": f"Delete {name}",
                "parameters": [id_param],
                "responses": {"200": {"description": "Record deleted"}},
            }

        if detail_paths:
            spec["paths"][item_path] = detail_paths

        # 3. Custom @action / @route endpoints
        for attr_name in dir(resource):
            attr = getattr(resource, attr_name)
            if callable(attr) and getattr(attr, "_is_action", False):
                act_methods = getattr(attr, "_action_methods", ["GET"])
                act_detail = getattr(attr, "_action_detail", False)
                act_url_path = getattr(attr, "_action_url_path", attr_name).strip("/")

                if resource.singleton:
                    act_route = f"{base_path}/{act_url_path}"
                else:
                    act_route = f"{item_path}/{act_url_path}" if act_detail else f"{base_path}/{act_url_path}"

                if act_route not in spec["paths"]:
                    spec["paths"][act_route] = {}

                for m in act_methods:
                    m_lower = m.lower()
                    op: Dict[str, Any] = {
                        "tags": [name.capitalize()],
                        "summary": f"{name.capitalize()} {attr_name.replace('_', ' ')}",
                        "responses": {"200": {"description": "Successful operation"}},
                    }
                    if act_detail and not resource.singleton:
                        op["parameters"] = [id_param]
                    if m_lower in ("post", "put", "patch"):
                        op["requestBody"] = {
                            "required": True,
                            "content": {"application/json": {"schema": {"type": "object"}}},
                        }
                    if resource.auth_required and api.auth:
                        op.setdefault("security", [{"bearerAuth": []}])
                    spec["paths"][act_route][m_lower] = op

        # Add security tag if auth enabled
        if resource.auth_required and api.auth:
            for p_dict in (coll_paths, detail_paths):
                for op in p_dict.values():
                    op.setdefault("security", [{"bearerAuth": []}])

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

