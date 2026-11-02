"""Test suite for auto OpenAPI doc generation and error_responses features in flask-build-api."""

from flask import Flask, Blueprint
from flask_sqlalchemy import SQLAlchemy
import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from flask_build_api import (
    Api,
    ApiError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
    ValidationErrorResponse,
    action,
    format_error_response,
    generate_openapi_spec,
    mount_swagger_docs,
    register_error_handlers,
)


class Base(DeclarativeBase):
    pass

db = SQLAlchemy(model_class=Base)

class Widget(Base):
    __tablename__ = "widgets"
    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    name: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    price: Mapped[float] = mapped_column(sa.Float, default=0.0)


app = Flask(__name__)
app.config.update(
    TESTING=True,
    SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
    SECRET_KEY="test-docs-and-errors-secret",
)
db.init_app(app)

# 1. Initialize Api with auto docs enabled (docs=True)
api = Api(app, prefix="/api", docs=True, docs_url="/docs", docs_title="Widget Store API")

# 2. Create resource with custom error_responses
class CustomWidgetResource:
    model = Widget
    name = "widgets"
    error_responses = {409: "Conflict - duplicate widget name"}

    @action(methods=["POST"], detail=True, url_path="restock", error_responses={429: "Restock rate limit exceeded"})
    def restock(self, instance, data=None):
        return {"restocked": True}

    @action(methods=["GET"], detail=False, url_path="crash")
    def trigger_error(self):
        raise ForbiddenError("Direct custom forbidden error")

widget_api = api.create_resource(
    model=Widget,
    name="widgets",
    error_responses={409: "Conflict - duplicate widget name"},
)

# Route outside resource to verify register_error_handlers
@app.route("/standalone-error")
def standalone_error_view():
    raise NotFoundError("Widget not found in warehouse")


with app.app_context():
    db.create_all()

client = app.test_client()


def test_auto_openapi_docs():
    print("\n--- TEST 1: Auto OpenAPI Specification & Swagger UI ---")
    # 1.1 GET /docs/openapi.json
    res_spec = client.get("/docs/openapi.json")
    assert res_spec.status_code == 200
    assert res_spec.is_json
    spec = res_spec.get_json()

    assert spec["openapi"] == "3.0.0"
    assert spec["info"]["title"] == "Widget Store API"
    assert "/api/widgets" in spec["paths"]
    assert "/api/widgets/{identifier}" in spec["paths"]
    print("[PASS] OpenAPI 3.0.0 JSON generated with registered resource paths.")

    # 1.2 Verify components.schemas contains ErrorDetail and ErrorResponse
    schemas = spec["components"]["schemas"]
    assert "ErrorDetail" in schemas
    assert "ErrorResponse" in schemas
    assert schemas["ErrorResponse"]["properties"]["success"]["example"] is False
    print("[PASS] OpenAPI components.schemas includes standardized ErrorResponse.")

    # 1.3 Verify components.responses contains standard error responses
    comp_responses = spec["components"]["responses"]
    assert "400BadRequest" in comp_responses
    assert "401Unauthorized" in comp_responses
    assert "403Forbidden" in comp_responses
    assert "404NotFound" in comp_responses
    assert "422ValidationError" in comp_responses
    assert "500InternalError" in comp_responses
    print("[PASS] OpenAPI components.responses includes all standard error responses.")

    # 1.4 Verify mutating endpoints include 400 and 422 error references
    post_responses = spec["paths"]["/api/widgets"]["post"]["responses"]
    assert "201" in post_responses
    assert "400" in post_responses
    assert "422" in post_responses
    # Verify custom resource error_responses
    assert "409" in post_responses
    print("[PASS] Resource POST endpoint includes 400, 422, and custom 409 error responses.")

    # 1.5 Verify detail endpoints include 404
    get_detail_responses = spec["paths"]["/api/widgets/{identifier}"]["get"]["responses"]
    assert "404" in get_detail_responses
    print("[PASS] Detail GET endpoint includes 404 error response.")

    # 1.6 Verify Swagger UI HTML page at /docs
    res_ui = client.get("/docs")
    assert res_ui.status_code == 200
    assert "SwaggerUIBundle" in res_ui.get_data(as_text=True)
    assert "/docs/openapi.json" in res_ui.get_data(as_text=True)
    print("[PASS] Swagger UI page successfully mounted and rendered at /docs.")


def test_standardized_error_responses():
    print("\n--- TEST 2: Standardized JSON Error Responses ---")
    # 2.1 Unhandled route error outside resource (handled by register_error_handlers)
    res = client.get("/standalone-error")
    assert res.status_code == 404
    assert res.is_json
    body = res.get_json()
    assert body["success"] is False
    assert body["error"]["code"] == "not_found"
    assert "Widget not found" in body["error"]["message"]
    print("[PASS] register_error_handlers captured standalone ApiError and returned standard JSON.")

    # 2.2 Format error response directly
    with app.app_context():
        json_resp, status, headers = format_error_response(
            message="Manual validation error",
            code="validation_error",
            details={"field": ["Required"]},
            status_code=422,
            request_id="req-12345",
        )
    assert status == 422
    assert headers["X-Request-ID"] == "req-12345"
    print("[PASS] format_error_response produces standard tuple with X-Request-ID.")


if __name__ == "__main__":
    test_auto_openapi_docs()
    test_standardized_error_responses()
    print("\n=======================================================")
    print("ALL OPENAPI DOCS & ERROR RESPONSES TESTS PASSED!")
    print("=======================================================\n")
