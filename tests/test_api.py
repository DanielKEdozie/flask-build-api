import json
import pytest
from flask import Flask, Blueprint
from flask_sqlalchemy import SQLAlchemy
from marshmallow import Schema, fields

from flask_build_api import Api, ApiMixin, action, ApiAuth

db = SQLAlchemy()


# Models for testing
class Product(db.Model):
    __tablename__ = "products"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    slug = db.Column(db.String(100), unique=True, nullable=False)
    price = db.Column(db.Float, default=0.0)
    category = db.Column(db.String(50), default="parts")
    is_deleted = db.Column(db.Boolean, default=False)


# Schemas
class ProductSchema(Schema):
    id = fields.Int(dump_only=True)
    name = fields.Str(required=True)
    slug = fields.Str(required=True)
    price = fields.Float()
    category = fields.Str()


class ProductCreateSchema(Schema):
    name = fields.Str(required=True)
    slug = fields.Str(required=True)
    price = fields.Float()


class ProductUpdateSchema(Schema):
    name = fields.Str()
    price = fields.Float()


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["TESTING"] = True

    db.init_app(app)

    with app.app_context():
        db.create_all()
        # Seed test data
        p1 = Product(name="Brake Pad Front", slug="brake-pad-front", price=45.0, category="brakes")
        p2 = Product(name="Brake Disc Rear", slug="brake-disc-rear", price=80.0, category="brakes")
        p3 = Product(name="Oil Filter", slug="oil-filter", price=12.0, category="filters")
        db.session.add_all([p1, p2, p3])
        db.session.commit()

    yield app


def test_factory_resource_crud(app):
    api_bp = Blueprint("api", __name__, url_prefix="/api")
    api = Api(api_bp)

    api.create_resource(
        model=Product,
        schema=ProductSchema,
        name="products",
        pk_field="id",
        pk_type="int",
        fallback_pk="slug",
        fallback_type="string",
        filter_fields=["category"],
        search_fields=["name"],
    )

    app.register_blueprint(api_bp)
    client = app.test_client()

    # 1. List
    res = client.get("/api/products")
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert len(data["data"]) == 3
    assert data["meta"]["total"] == 3

    # 2. Retrieve by primary key (id)
    res_id = client.get("/api/products/1")
    assert res_id.status_code == 200
    assert res_id.get_json()["data"]["name"] == "Brake Pad Front"

    # 3. Retrieve by fallback key (slug)
    res_slug = client.get("/api/products/oil-filter")
    assert res_slug.status_code == 200
    assert res_slug.get_json()["data"]["name"] == "Oil Filter"

    # 4. Filter
    res_filter = client.get("/api/products?category=filters")
    assert res_filter.status_code == 200
    assert len(res_filter.get_json()["data"]) == 1

    # 5. Search
    res_search = client.get("/api/products?q=Disc")
    assert res_search.status_code == 200
    assert len(res_search.get_json()["data"]) == 1

    # 6. Create
    new_prod = {"name": "Air Filter", "slug": "air-filter", "price": 18.0}
    res_post = client.post("/api/products", json=new_prod)
    assert res_post.status_code == 201
    assert res_post.get_json()["data"]["slug"] == "air-filter"

    # 7. Partial update (patch)
    res_patch = client.patch("/api/products/air-filter", json={"price": 22.5})
    assert res_patch.status_code == 200
    assert res_patch.get_json()["data"]["price"] == 22.5

    # 8. Delete
    res_del = client.delete("/api/products/air-filter")
    assert res_del.status_code == 200
    res_check = client.get("/api/products/air-filter")
    assert res_check.status_code == 404


def test_class_based_mixin_with_action_and_hooks(app):
    api_bp = Blueprint("api_v2", __name__, url_prefix="/v2")
    api = Api(api_bp)

    hook_tracker = []

    class CustomProductApi(ApiMixin):
        model = Product
        schema = ProductSchema
        name = "products"
        pk_field = "id"
        fallback_pk = "slug"
        schema_overrides = {
            "create": {"req": ProductCreateSchema, "res": ProductSchema},
            "update": (ProductUpdateSchema, ProductSchema),
        }

        def before_create_load(self, data, **kwargs):
            hook_tracker.append("before_create_load")
            data["name"] = data["name"].strip()
            return data

        def after_create_load(self, data, **kwargs):
            hook_tracker.append("after_create_load")
            return data

        @action(methods=["POST"], detail=True, url_path="apply-discount")
        def discount(self, instance):
            instance.price = round(instance.price * 0.9, 2)
            db.session.commit()
            return {"new_price": instance.price}

    api.register_resource(CustomProductApi)
    app.register_blueprint(api_bp)
    client = app.test_client()

    # Test create with hooks
    payload = {"name": "   Fuel Pump   ", "slug": "fuel-pump", "price": 120.0}
    res = client.post("/v2/products", json=payload)
    assert res.status_code == 201
    assert "before_create_load" in hook_tracker
    assert "after_create_load" in hook_tracker
    assert res.get_json()["data"]["name"] == "Fuel Pump"

    # Test custom @action endpoint
    res_action = client.post("/v2/products/fuel-pump/apply-discount")
    assert res_action.status_code == 200
    assert res_action.get_json()["data"]["new_price"] == 108.0


def test_docs_and_openapi(app):
    api = Api(app, prefix="/api")
    api.create_resource(model=Product, schema=ProductSchema, name="products")
    api.mount_docs(url_prefix="/api/docs")

    client = app.test_client()
    res = client.get("/api/docs/openapi.json")
    assert res.status_code == 200
    spec = res.get_json()
    assert spec["openapi"] == "3.0.0"
    assert "/api/products" in spec["paths"]
    assert "/api/products/{identifier}" in spec["paths"]

