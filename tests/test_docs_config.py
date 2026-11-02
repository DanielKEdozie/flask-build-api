"""Test suite verifying OpenAPI / Swagger documentation configuration via Flask app.config.
Tests:
- API_KEY
- API_TITLE
- API_URL
- API_DESCRIPTION
- API_VERSION
- API_LICENSE
- API_DOCS_ENABLED / API_DOCS
- API_TERMS_OF_SERVICE
- API_CONTACT
"""

from flask import Flask
from flask_sqlalchemy import SQLAlchemy
import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from flask_build_api import Api, generate_openapi_spec


class Base(DeclarativeBase):
    pass

db = SQLAlchemy(model_class=Base)

class Product(Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    title: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    price: Mapped[float] = mapped_column(sa.Float, default=0.0)


# 1. Setup Flask App with Config keys
app = Flask(__name__)
app.config.update(
    TESTING=True,
    SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
    # Config keys requested by user:
    API_TITLE="BuyAutoParts Enterprise API",
    API_VERSION="3.5.1",
    API_DESCRIPTION="High performance automotive parts catalog & inventory API.",
    API_URL="https://api.buyautoparts.com/v1",
    API_LICENSE={"name": "MIT License", "url": "https://opensource.org/licenses/MIT"},
    API_KEY="X-API-Key",
    API_TERMS_OF_SERVICE="https://buyautoparts.com/terms",
    API_CONTACT={"name": "API Support", "email": "support@buyautoparts.com"},
    # Auto-activation via config:
    API_DOCS_ENABLED=True,
    API_DOCS_URL="/api-docs",
)
db.init_app(app)

# 2. Initialize Api without passing docs arguments (purely driven by app.config)
api = Api(app, prefix="/api")

product_api = api.create_resource(
    model=Product,
    name="products",
    auth_required=True,
)


with app.app_context():
    db.create_all()

client = app.test_client()


def test_docs_configured_via_app_config():
    print("\n--- TEST: OpenAPI Specification via app.config ---")

    # 1. Verify docs automatically mounted at configured API_DOCS_URL
    res_ui = client.get("/api-docs")
    assert res_ui.status_code == 200
    assert "BuyAutoParts Enterprise API" in res_ui.get_data(as_text=True)
    assert "/api-docs/openapi.json" in res_ui.get_data(as_text=True)
    print("[PASS] Docs automatically mounted at app.config['API_DOCS_URL'] (/api-docs).")

    # 2. Verify OpenAPI JSON spec
    res_spec = client.get("/api-docs/openapi.json")
    assert res_spec.status_code == 200
    assert res_spec.is_json
    spec = res_spec.get_json()

    # 2.1 API_TITLE
    assert spec["info"]["title"] == "BuyAutoParts Enterprise API"
    print("[PASS] API_TITLE correctly populated in info.title.")

    # 2.2 API_VERSION
    assert spec["info"]["version"] == "3.5.1"
    print("[PASS] API_VERSION correctly populated in info.version.")

    # 2.3 API_DESCRIPTION
    assert spec["info"]["description"] == "High performance automotive parts catalog & inventory API."
    print("[PASS] API_DESCRIPTION correctly populated in info.description.")

    # 2.4 API_URL in servers
    assert "servers" in spec
    assert spec["servers"][0]["url"] == "https://api.buyautoparts.com/v1"
    print("[PASS] API_URL correctly populated in servers[0].url.")

    # 2.5 API_LICENSE
    assert spec["info"]["license"]["name"] == "MIT License"
    assert spec["info"]["license"]["url"] == "https://opensource.org/licenses/MIT"
    print("[PASS] API_LICENSE correctly populated in info.license.")

    # 2.6 API_TERMS_OF_SERVICE & API_CONTACT
    assert spec["info"]["termsOfService"] == "https://buyautoparts.com/terms"
    assert spec["info"]["contact"]["email"] == "support@buyautoparts.com"
    print("[PASS] API_TERMS_OF_SERVICE and API_CONTACT populated.")

    # 2.7 API_KEY in components.securitySchemes
    sec_schemes = spec["components"]["securitySchemes"]
    assert "apiKeyAuth" in sec_schemes
    assert sec_schemes["apiKeyAuth"]["type"] == "apiKey"
    assert sec_schemes["apiKeyAuth"]["name"] == "X-API-Key"
    assert sec_schemes["apiKeyAuth"]["in"] == "header"
    print("[PASS] API_KEY correctly configured components.securitySchemes.apiKeyAuth.")

    # 2.8 Verify auth_required resource operations include apiKeyAuth
    get_op = spec["paths"]["/api/products"]["get"]
    assert any("apiKeyAuth" in sec_item for sec_item in get_op["security"])
    print("[PASS] Protected operations automatically include apiKeyAuth security requirement.")


def test_string_license_and_direct_spec_generator():
    print("\n--- TEST: Direct Specification Generator & String License ---")
    app2 = Flask("app2")
    app2.config.update(
        API_TITLE="Test Simple API",
        API_VERSION="1.2.3",
        API_LICENSE="Apache-2.0",
        API_KEY="api_key_query",
        API_KEY_IN="query",
    )
    api2 = Api(app2)

    with app2.app_context():
        spec = generate_openapi_spec(api2)
        assert spec["info"]["title"] == "Test Simple API"
        assert spec["info"]["version"] == "1.2.3"
        assert spec["info"]["license"]["name"] == "Apache-2.0"
        assert spec["components"]["securitySchemes"]["apiKeyAuth"]["name"] == "api_key_query"
        assert spec["components"]["securitySchemes"]["apiKeyAuth"]["in"] == "query"
    print("[PASS] String license and query API key correctly formatted.")


if __name__ == "__main__":
    test_docs_configured_via_app_config()
    test_string_license_and_direct_spec_generator()
    print("\n=======================================================")
    print("ALL CONFIG-DRIVEN OPENAPI TESTS PASSED PERFECTLY!")
    print("=======================================================\n")
