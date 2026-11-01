import unittest
from flask import Flask, Blueprint
from flask_sqlalchemy import SQLAlchemy
from marshmallow import Schema, fields

from flask_build_api import Api, ApiMixin, action, route, ApiAuth, SchemaMixin, create_schema

db = SQLAlchemy()


class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    orders = db.relationship("Order", backref="user", lazy=True)


class Order(db.Model):
    __tablename__ = "orders"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    total = db.Column(db.Float, default=0.0)


class Product(db.Model):
    __tablename__ = "products"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    slug = db.Column(db.String(100), unique=True, nullable=False)
    price = db.Column(db.Float, default=0.0)
    category = db.Column(db.String(50), default="parts")
    is_deleted = db.Column(db.Boolean, default=False)


class UserSchema(Schema):
    id = fields.Int(dump_only=True)
    name = fields.Str(required=True)


class OrderSchema(Schema):
    id = fields.Int(dump_only=True)
    user_id = fields.Int(dump_only=True)
    total = fields.Float(required=True)


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


class DiscountPayloadSchema(Schema):
    percentage = fields.Float(required=True)



class Profile(db.Model):
    __tablename__ = "profiles"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), unique=True, nullable=False)
    bio = db.Column(db.String(255), default="")


class Settings(db.Model):
    __tablename__ = "settings"
    id = db.Column(db.Integer, primary_key=True)
    site_name = db.Column(db.String(100), default="My Shop")
    maintenance = db.Column(db.Boolean, default=False)


class ProfileSchema(Schema):
    id = fields.Int(dump_only=True)
    user_id = fields.Int(dump_only=True)
    bio = fields.Str()


class SettingsSchema(Schema):
    id = fields.Int(dump_only=True)
    site_name = fields.Str()
    maintenance = fields.Bool()


class TestFlaskBuildApi(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
        self.app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
        self.app.config["TESTING"] = True

        db.init_app(self.app)

        with self.app.app_context():
            db.create_all()
            p1 = Product(name="Brake Pad Front", slug="brake-pad-front", price=45.0, category="brakes")
            p2 = Product(name="Brake Disc Rear", slug="brake-disc-rear", price=80.0, category="brakes")
            p3 = Product(name="Oil Filter", slug="oil-filter", price=12.0, category="filters")
            db.session.add_all([p1, p2, p3])

            u1 = User(name="Alice")
            u2 = User(name="Bob")
            db.session.add_all([u1, u2])
            db.session.commit()

            o1 = Order(user_id=u1.id, total=150.0)
            o2 = Order(user_id=u2.id, total=250.0)
            db.session.add_all([o1, o2])
            db.session.commit()

    def test_factory_resource_crud(self):
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

        self.app.register_blueprint(api_bp)
        client = self.app.test_client()

        # 1. List (wrap_pagination_in_envelope = False by default)
        res = client.get("/api/products")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertIsInstance(data["data"], list)
        self.assertEqual(len(data["data"]), 3)
        self.assertEqual(data["meta"]["total"], 3)
        self.assertIn("X-Request-ID", res.headers)

        # 1b. Test ?paginate=false
        res_unpaginated = client.get("/api/products?paginate=false")
        self.assertEqual(res_unpaginated.status_code, 200)
        data_unpaginated = res_unpaginated.get_json()
        self.assertEqual(len(data_unpaginated["data"]), 3)
        self.assertNotIn("meta", data_unpaginated)

        # 1c. Test sparse fieldsets ?fields=name,price
        res_fields = client.get("/api/products?fields=name,price")
        self.assertEqual(res_fields.status_code, 200)
        item0 = res_fields.get_json()["data"][0]
        self.assertIn("name", item0)
        self.assertIn("price", item0)
        self.assertNotIn("slug", item0)

        # 2. Retrieve by primary key (id) + ETag check
        res_id = client.get("/api/products/1")
        self.assertEqual(res_id.status_code, 200)
        self.assertEqual(res_id.get_json()["data"]["name"], "Brake Pad Front")
        self.assertIn("ETag", res_id.headers)
        etag = res_id.headers["ETag"]

        # 2b. Test If-None-Match 304
        res_304 = client.get("/api/products/1", headers={"If-None-Match": etag})
        self.assertEqual(res_304.status_code, 304)

        # 3. Retrieve by fallback key (slug)
        res_slug = client.get("/api/products/oil-filter")
        self.assertEqual(res_slug.status_code, 200)
        self.assertEqual(res_slug.get_json()["data"]["name"], "Oil Filter")

        # 4. Filter
        res_filter = client.get("/api/products?category=filters")
        self.assertEqual(res_filter.status_code, 200)
        self.assertEqual(len(res_filter.get_json()["data"]), 1)

        # 5. Search
        res_search = client.get("/api/products?q=Disc")
        self.assertEqual(res_search.status_code, 200)
        self.assertEqual(len(res_search.get_json()["data"]), 1)

        # 6. Create with Idempotency-Key
        new_prod = {"name": "Air Filter", "slug": "air-filter", "price": 18.0}
        res_post = client.post("/api/products", json=new_prod, headers={"Idempotency-Key": "key-12345"})
        self.assertEqual(res_post.status_code, 201)
        self.assertEqual(res_post.get_json()["data"]["slug"], "air-filter")

        # 6b. Retry same Create with identical Idempotency-Key -> returns cached response, doesn't crash on duplicate slug
        res_post_retry = client.post("/api/products", json=new_prod, headers={"Idempotency-Key": "key-12345"})
        self.assertEqual(res_post_retry.status_code, 201)
        self.assertEqual(res_post_retry.get_json()["data"]["slug"], "air-filter")

        # 7. Partial update (patch) with If-Match concurrency check
        product_etag = res_post.headers.get("ETag")
        # Stale If-Match fails with 412
        res_conflict = client.patch(
            "/api/products/air-filter",
            json={"price": 30.0},
            headers={"If-Match": '"wrong-etag"'},
        )
        self.assertEqual(res_conflict.status_code, 412)

        # Correct If-Match succeeds
        res_patch = client.patch(
            "/api/products/air-filter",
            json={"price": 22.5},
            headers={"If-Match": product_etag} if product_etag else {},
        )
        self.assertEqual(res_patch.status_code, 200)
        self.assertEqual(res_patch.get_json()["data"]["price"], 22.5)

        # 7b. Bulk create
        bulk_data = [
            {"name": "Spark Plug 1", "slug": "spark-plug-1", "price": 8.0},
            {"name": "Spark Plug 2", "slug": "spark-plug-2", "price": 8.0},
        ]
        res_bulk_c = client.post("/api/products/bulk", json=bulk_data)
        self.assertEqual(res_bulk_c.status_code, 201)
        self.assertEqual(len(res_bulk_c.get_json()["data"]), 2)

        # 7c. Bulk delete
        res_bulk_del = client.post("/api/products/bulk-delete", json={"ids": [res_bulk_c.get_json()["data"][0]["id"], res_bulk_c.get_json()["data"][1]["id"]]})
        self.assertEqual(res_bulk_del.status_code, 200)
        self.assertEqual(res_bulk_del.get_json()["count"], 2)

        # 8. Delete
        res_del = client.delete("/api/products/air-filter")
        self.assertEqual(res_del.status_code, 200)
        res_check = client.get("/api/products/air-filter")
        self.assertEqual(res_check.status_code, 404)

    def test_class_based_mixin_with_action_and_hooks(self):
        api_bp = Blueprint("api_v2", __name__, url_prefix="/v2")
        api = Api(api_bp)

        hook_tracker = []
        ctx_audit = []

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

            def before_create_load(self, data, ctx=None):
                hook_tracker.append("before_create_load")
                data["name"] = data["name"].strip()
                if ctx:
                    ctx["source"] = "mobile_client"
                return data

            def after_create_load(self, data, ctx=None):
                hook_tracker.append("after_create_load")
                if ctx and "source" in ctx:
                    ctx_audit.append(ctx["source"])
                return data

            def before_create(self, instance, ctx=None):
                hook_tracker.append("before_create")

            def after_create(self, instance, ctx=None):
                hook_tracker.append("after_create")

            def before_update(self, instance, partial=False, ctx=None):
                hook_tracker.append(f"before_update:partial={partial}")

            def after_update(self, instance, partial=False, ctx=None):
                hook_tracker.append(f"after_update:partial={partial}")

            def before_delete(self, instance, ctx=None):
                hook_tracker.append("before_delete")

            def after_delete(self, instance, ctx=None):
                hook_tracker.append("after_delete")

            @action(methods=["POST"], detail=True, url_path="apply-discount")
            def discount(self, instance, ctx=None):
                instance.price = round(instance.price * 0.9, 2)
                db.session.commit()
                return {
                    "new_price": instance.price,
                    "ctx_action": ctx.action if ctx else None,
                    "ctx_model": ctx.model.__name__ if ctx else None,
                }

            # @route alias with schema validation
            @route(methods=["POST"], detail=True, url_path="custom-discount", schema=DiscountPayloadSchema, validate_schema=True)
            def custom_discount(self, instance, data=None, ctx=None):
                rate = data["percentage"]
                instance.price = round(instance.price * (1 - rate / 100), 2)
                db.session.commit()
                return {
                    "new_price": instance.price,
                    "rate_applied": rate,
                    "ctx_has_data": ctx.data is not None if ctx else False,
                }

        api.register_resource(CustomProductApi)
        self.app.register_blueprint(api_bp)
        client = self.app.test_client()

        # Test create with hooks & ctx
        payload = {"name": "   Fuel Pump   ", "slug": "fuel-pump", "price": 120.0}
        res = client.post("/v2/products", json=payload)
        self.assertEqual(res.status_code, 201)
        self.assertIn("before_create_load", hook_tracker)
        self.assertIn("after_create_load", hook_tracker)
        self.assertIn("before_create", hook_tracker)
        self.assertIn("after_create", hook_tracker)
        self.assertIn("mobile_client", ctx_audit)
        self.assertEqual(res.get_json()["data"]["name"], "Fuel Pump")

        # Test custom @action endpoint with ctx
        res_action = client.post("/v2/products/fuel-pump/apply-discount")
        self.assertEqual(res_action.status_code, 200)
        action_data = res_action.get_json()["data"]
        self.assertEqual(action_data["new_price"], 108.0)
        self.assertEqual(action_data["ctx_action"], "discount")
        self.assertEqual(action_data["ctx_model"], "Product")

        # Test @route alias with schema validation failure (422)
        res_invalid = client.post("/v2/products/fuel-pump/custom-discount", json={"percentage": "not_a_number"})
        self.assertEqual(res_invalid.status_code, 422)
        self.assertFalse(res_invalid.get_json()["success"])
        self.assertIn("percentage", res_invalid.get_json()["errors"])

        # Test @route alias with schema validation success (200)
        res_valid = client.post("/v2/products/fuel-pump/custom-discount", json={"percentage": 50.0})
        self.assertEqual(res_valid.status_code, 200)
        custom_data = res_valid.get_json()["data"]
        self.assertEqual(custom_data["new_price"], 54.0)
        self.assertEqual(custom_data["rate_applied"], 50.0)
        self.assertTrue(custom_data["ctx_has_data"])

        # Test update (PUT) triggers before_update/after_update with partial=False
        res_put = client.put("/v2/products/fuel-pump", json={"name": "Fuel Pump HD", "price": 150.0})
        self.assertEqual(res_put.status_code, 200)
        self.assertIn("before_update:partial=False", hook_tracker)
        self.assertIn("after_update:partial=False", hook_tracker)

        # Test partial_update (PATCH) triggers before_update/after_update with partial=True
        res_patch = client.patch("/v2/products/fuel-pump", json={"price": 140.0})
        self.assertEqual(res_patch.status_code, 200)
        self.assertIn("before_update:partial=True", hook_tracker)
        self.assertIn("after_update:partial=True", hook_tracker)

        # Test delete triggers before_delete and after_delete
        res_del = client.delete("/v2/products/fuel-pump")
        self.assertEqual(res_del.status_code, 200)
        self.assertIn("before_delete", hook_tracker)
        self.assertIn("after_delete", hook_tracker)

    def test_create_nested_resource(self):
        api_bp = Blueprint("api_nested", __name__, url_prefix="/nested")
        api = Api(api_bp)

        user_api = api.create_resource(model=User, schema=UserSchema, name="users")

        parent_captured = []

        class NestedOrderApi(ApiMixin):
            model = Order
            schema = OrderSchema
            name = "orders"
            parent_pk_field = "user_id"
            resolve_parent_pk = {"user_id": lambda uid: db.session.get(User, int(uid))}

            def before_create_load(self, data, ctx=None):
                if ctx and ctx.parent:
                    parent_captured.append(ctx.parent.name)
                return data

            @action(methods=["GET"], detail=False, url_path="parent-info")
            def parent_info(self, ctx=None):
                p = ctx.parent if ctx else None
                return {"parent_user_name": p.name if p else None}

        order_api = user_api.create_resource(
            model=Order,
            schema=OrderSchema,
            name="orders",
            parent_pk_field="user_id",
            resolve_parent_pk={"user_id": lambda uid: db.session.get(User, int(uid))},
            before_create_load=lambda data, ctx=None: parent_captured.append(ctx.parent.name) or data if (ctx and ctx.parent) else data,
        )

        self.app.register_blueprint(api_bp)
        client = self.app.test_client()

        # 1. List Alice's orders (user_id = 1)
        res_u1 = client.get("/nested/users/1/orders")
        self.assertEqual(res_u1.status_code, 200)
        data_u1 = res_u1.get_json()
        self.assertEqual(len(data_u1["data"]), 1)
        self.assertEqual(data_u1["data"][0]["total"], 150.0)

        # 2. List Bob's orders (user_id = 2)
        res_u2 = client.get("/nested/users/2/orders")
        self.assertEqual(res_u2.status_code, 200)
        data_u2 = res_u2.get_json()
        self.assertEqual(len(data_u2["data"]), 1)
        self.assertEqual(data_u2["data"][0]["total"], 250.0)

        # 3. Create order for Alice -> parent is resolved and user_id is automatically injected!
        res_create = client.post("/nested/users/1/orders", json={"total": 99.99})
        self.assertEqual(res_create.status_code, 201)
        created_id = res_create.get_json()["data"]["id"]
        self.assertIn("Alice", parent_captured)

        # 4. Check custom action accessing parent
        # 5. Non-existent parent returns 404
        res_invalid_parent = client.get("/nested/users/9999/orders")
        self.assertEqual(res_invalid_parent.status_code, 404)

        # 6. Retrieve single order
        res_get = client.get(f"/nested/users/1/orders/{created_id}")
        self.assertEqual(res_get.status_code, 200)
        self.assertEqual(res_get.get_json()["data"]["total"], 99.99)

        # 7. IDOR safety: Bob cannot access Alice's order
        res_wrong_user = client.get(f"/nested/users/2/orders/{created_id}")
        self.assertEqual(res_wrong_user.status_code, 404)

    def test_custom_data_envelope_key(self):
        bp = Blueprint("api_custom_env", __name__, url_prefix="/api_custom_env")
        api = Api(bp)
        api.create_resource(
            model=Product,
            schema=ProductSchema,
            name="products",
            data_envelope_key="result",
        )
        self.app.register_blueprint(bp)

        client = self.app.test_client()
        res = client.get("/api_custom_env/products")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertIn("result", data)
        self.assertNotIn("data", data)
        self.assertEqual(len(data["result"]), 3)

    def test_singleton_resources(self):
        # 1. Standalone Singleton Resource Setup
        bp_singleton = Blueprint("api_singleton", __name__, url_prefix="/api_singleton")
        api = Api(bp_singleton)
        api.create_resource(
            model=Settings,
            schema=SettingsSchema,
            name="settings",
            singleton=True,
        )

        # 2. Nested 1-to-1 Singleton Resource Setup
        bp_nested_sing = Blueprint("api_nested_sing", __name__, url_prefix="/nested_sing")
        api_nested = Api(bp_nested_sing)
        user_res = api_nested.create_resource(
            model=User,
            schema=UserSchema,
            name="users",
        )
        user_res.create_resource(
            model=Profile,
            schema=ProfileSchema,
            name="profile",
            singleton=True,
            parent_pk_field="user_id",
            resolve_parent_pk={"user_id": lambda uid: db.session.get(User, int(uid))},
        )

        # Register both blueprints before client makes requests
        self.app.register_blueprint(bp_singleton)
        self.app.register_blueprint(bp_nested_sing)
        client = self.app.test_client()

        # 1a. GET non-existent singleton -> 404
        res_get_init = client.get("/api_singleton/settings")
        self.assertEqual(res_get_init.status_code, 404)

        # 1b. POST create singleton -> 201
        res_create = client.post("/api_singleton/settings", json={"site_name": "Antigravity Parts", "maintenance": False})
        self.assertEqual(res_create.status_code, 201)
        self.assertEqual(res_create.get_json()["data"]["site_name"], "Antigravity Parts")

        # 1c. POST again -> 409 Conflict
        res_dup = client.post("/api_singleton/settings", json={"site_name": "Duplicate"})
        self.assertEqual(res_dup.status_code, 409)

        # 1d. GET singleton -> 200
        res_get = client.get("/api_singleton/settings")
        self.assertEqual(res_get.status_code, 200)
        self.assertEqual(res_get.get_json()["data"]["site_name"], "Antigravity Parts")

        # 1e. PATCH update singleton -> 200
        res_patch = client.patch("/api_singleton/settings", json={"maintenance": True})
        self.assertEqual(res_patch.status_code, 200)
        self.assertEqual(res_patch.get_json()["data"]["maintenance"], True)

        # 1f. DELETE singleton -> 200
        res_del = client.delete("/api_singleton/settings")
        self.assertEqual(res_del.status_code, 200)

        # 1g. GET after delete -> 404
        res_after = client.get("/api_singleton/settings")
        self.assertEqual(res_after.status_code, 404)

        # --- Nested Singleton Tests ---
        # 2a. GET Alice's profile (user_id = 1) -> 404
        res_u1_init = client.get("/nested_sing/users/1/profile")
        self.assertEqual(res_u1_init.status_code, 404)

        # 2b. POST Alice's profile -> 201
        res_u1_create = client.post("/nested_sing/users/1/profile", json={"bio": "Alice In Wonderland"})
        self.assertEqual(res_u1_create.status_code, 201)
        self.assertEqual(res_u1_create.get_json()["data"]["bio"], "Alice In Wonderland")
        self.assertEqual(res_u1_create.get_json()["data"]["user_id"], 1)

        # 2c. POST again for Alice -> 409 Conflict
        res_u1_dup = client.post("/nested_sing/users/1/profile", json={"bio": "Duplicate Bio"})
        self.assertEqual(res_u1_dup.status_code, 409)

        # 2d. Bob's profile is still 404 (IDOR / isolation test)
        res_u2_get = client.get("/nested_sing/users/2/profile")
        self.assertEqual(res_u2_get.status_code, 404)

        # 2e. PATCH Alice's profile -> 200
        res_u1_patch = client.patch("/nested_sing/users/1/profile", json={"bio": "Alice The Engineer"})
        self.assertEqual(res_u1_patch.status_code, 200)
        self.assertEqual(res_u1_patch.get_json()["data"]["bio"], "Alice The Engineer")

        # 2f. DELETE Alice's profile -> 200
        res_u1_del = client.delete("/nested_sing/users/1/profile")
        self.assertEqual(res_u1_del.status_code, 200)

        # 2g. GET Alice's profile after delete -> 404
        res_u1_after = client.get("/nested_sing/users/1/profile")
        self.assertEqual(res_u1_after.status_code, 404)

    def test_auto_schema_features(self):
        # 1. Class-based SchemaMixin with relationships & depth
        class AutoUserSchema(SchemaMixin):
            model = User
            fields = ["id", "name", "orders"]
            relationships = {"orders": {"only": ["id", "total"]}}
            depth = 1

        self.assertIn("id", AutoUserSchema._declared_fields)
        self.assertTrue(AutoUserSchema._declared_fields["id"].dump_only)
        self.assertIn("name", AutoUserSchema._declared_fields)
        self.assertTrue(AutoUserSchema._declared_fields["name"].required)
        self.assertIn("orders", AutoUserSchema._declared_fields)
        self.assertIsInstance(AutoUserSchema._declared_fields["orders"], fields.Nested)

        with self.app.app_context():
            u = db.session.get(User, 1)
            dumped = AutoUserSchema().dump(u)
            self.assertEqual(dumped["name"], "Alice")
            self.assertEqual(len(dumped["orders"]), 1)
            self.assertEqual(dumped["orders"][0]["total"], 150.0)

        # 2. Factory create_schema with custom fields, load_only, dump_only, and exclusions
        ProductAutoSchema = create_schema(
            Product,
            exclude=["is_deleted"],
            dump_only=["id"],
            load_only=["price"],
            custom_fields={"custom_tag": fields.Constant("antigravity")},
        )
        p_inst = ProductAutoSchema()
        self.assertIn("custom_tag", p_inst.fields)
        self.assertNotIn("is_deleted", p_inst.fields)
        self.assertTrue(p_inst.fields["price"].load_only)
        dumped_p = p_inst.dump({"name": "Spark Plug", "slug": "spark-plug", "price": 9.99})
        self.assertEqual(dumped_p["custom_tag"], "antigravity")
        self.assertNotIn("price", dumped_p)

        # 2b. api.create_schema attached to Api instance with relationship write option
        api_temp = Api(Flask(__name__))
        UserRelSchema = api_temp.create_schema(
            User,
            relationships={"orders": {"exclude": ["items"], "write": True}},
        )
        u_rel_inst = UserRelSchema()
        self.assertIn("orders", u_rel_inst.fields)
        self.assertFalse(u_rel_inst.fields["orders"].dump_only)

        # 3. Zero-boilerplate resource creation with omitted schema
        fresh_app = Flask(__name__)
        fresh_app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
        fresh_app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
        fresh_app.config["TESTING"] = True
        fresh_db = SQLAlchemy(fresh_app)

        class Item(fresh_db.Model):
            __tablename__ = "items"
            id = fresh_db.Column(fresh_db.Integer, primary_key=True)
            title = fresh_db.Column(fresh_db.String(50), nullable=False)
            qty = fresh_db.Column(fresh_db.Integer, default=1)

        with fresh_app.app_context():
            fresh_db.create_all()
            fresh_db.session.add(Item(title="Gadget", qty=5))
            fresh_db.session.commit()

        bp_auto = Blueprint("api_auto", __name__, url_prefix="/api_auto")
        api_auto = Api(bp_auto)
        # Note: schema argument is completely omitted!
        api_auto.create_resource(model=Item)

        fresh_app.register_blueprint(bp_auto)
        client = fresh_app.test_client()

        # Test list endpoint with auto-generated schema
        res_list = client.get("/api_auto/items")
        self.assertEqual(res_list.status_code, 200)
        items_data = res_list.get_json()["data"]
        self.assertEqual(len(items_data), 1)
        self.assertEqual(items_data[0]["title"], "Gadget")

        # Test create endpoint with auto-generated validation
        res_create = client.post("/api_auto/items", json={"title": "Widget", "qty": 10})
        self.assertEqual(res_create.status_code, 201)
        self.assertEqual(res_create.get_json()["data"]["title"], "Widget")

        # 4. Self-referential recursive schema (Category tree)
        class Category(fresh_db.Model):
            __tablename__ = "categories"
            id = fresh_db.Column(fresh_db.Integer, primary_key=True)
            name = fresh_db.Column(fresh_db.String(50), nullable=False)
            parent_id = fresh_db.Column(fresh_db.Integer, fresh_db.ForeignKey("categories.id"), nullable=True)
            children = fresh_db.relationship("Category", backref=fresh_db.backref("parent", remote_side=[id]))

        class AutoCategorySchema(SchemaMixin):
            model = Category
            fields = ["id", "name", "children"]
            recurse = ["children"]

        with fresh_app.app_context():
            fresh_db.create_all()
            root_c = Category(name="Electronics")
            comp_c = Category(name="Computers", parent=root_c)
            lap_c = Category(name="Laptops", parent=comp_c)
            fresh_db.session.add_all([root_c, comp_c, lap_c])
            fresh_db.session.commit()

            dumped_tree = AutoCategorySchema().dump(root_c)
            self.assertEqual(dumped_tree["name"], "Electronics")
            self.assertEqual(len(dumped_tree["children"]), 1)
            self.assertEqual(dumped_tree["children"][0]["name"], "Computers")
            self.assertEqual(len(dumped_tree["children"][0]["children"]), 1)
            self.assertEqual(dumped_tree["children"][0]["children"][0]["name"], "Laptops")
            self.assertEqual(dumped_tree["children"][0]["children"][0]["children"], [])

            # Also verify factory with recurse=True
            DynamicCatSchema = create_schema(Category, recurse=True)
            dyn_tree = DynamicCatSchema().dump(root_c)
            self.assertEqual(dyn_tree["name"], "Electronics")
            self.assertEqual(dyn_tree["children"][0]["name"], "Computers")

    def test_docs_and_openapi(self):
        api = Api(self.app, prefix="/api")
        api.create_resource(model=Product, schema=ProductSchema, name="products")
        api.mount_docs(url_prefix="/api/docs")

        client = self.app.test_client()
        res = client.get("/api/docs/openapi.json")
        self.assertEqual(res.status_code, 200)
        spec = res.get_json()
        self.assertEqual(spec["openapi"], "3.0.0")
        self.assertIn("/api/products", spec["paths"])
        self.assertIn("/api/products/{identifier}", spec["paths"])


if __name__ == "__main__":
    unittest.main()
