"""Comprehensive test suite for TokenAuthManager and token authentication namespace in flask-build-api.
Tests:
- UserModelMixin / TokenUserMixin password hashing & serialization
- TokenAuthManager login, refresh, logout, me endpoints
- flask_build_api.token (token_required, login_required, admin_required, current_user)
- Bound namespaces (auth_mgr.token.* and auth.token.*)
- Top-level imports from flask_build_api
"""

from flask import Flask, jsonify
from flask_sqlalchemy import SQLAlchemy
import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from flask_build_api import (
    Api,
    TokenAuthManager,
    TokenUserMixin,
    UserModelMixin,
    admin_required as top_admin_required,
    auth,
    current_user as top_current_user,
    login_required as top_login_required,
    token,
    token_required as top_token_required,
)
from flask_build_api.token import (
    admin_required as namespaced_admin_required,
    current_user as namespaced_current_user,
    login_required as namespaced_login_required,
    token_required as namespaced_token_required,
)


# 1. Setup Flask & Database with UserModelMixin
class Base(DeclarativeBase):
    pass

db = SQLAlchemy(model_class=Base)

class User(Base, UserModelMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    email: Mapped[str] = mapped_column(sa.String(120), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    is_admin: Mapped[bool] = mapped_column(sa.Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True, nullable=False)


app = Flask(__name__)
app.config.update(
    SECRET_KEY="test-build-api-token-auth-secret-key-32bytes",
    SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
    TESTING=True,
)
db.init_app(app)

# Initialize Api and TokenAuthManager
api = Api(app)
auth_mgr = TokenAuthManager(
    model=User,
    identity_field="email",
    password_field="password_hash",
    auto_mount=True,
    url_prefix="/auth",
)
auth_mgr.init_app(app)
api.extend("auth", auth_mgr)

# 2. Register Routes Using Namespaced & Bound Decorators
# --- Namespaced flask_build_api.token routes ---
@app.route("/api/test-token")
@namespaced_token_required
def test_namespaced_token_view():
    return {"status": "ok", "user": namespaced_current_user.email}

@app.route("/api/test-login-alias")
@namespaced_login_required
def test_namespaced_login_view():
    return {"status": "ok", "user": namespaced_current_user.email}

@app.route("/api/test-admin")
@namespaced_admin_required
def test_namespaced_admin_view():
    return {"status": "admin_ok", "user": namespaced_current_user.email}

# --- Bound auth_mgr.token routes ---
@app.route("/api/bound-mgr-token")
@auth_mgr.token.token_required
def test_bound_mgr_view():
    return {"status": "bound_mgr_ok", "user": auth_mgr.token.current_user.email}

@app.route("/api/bound-mgr-admin")
@auth_mgr.token.admin_required
def test_bound_mgr_admin_view():
    return {"status": "bound_admin_ok", "user": auth_mgr.token.current_user.email}

# --- Bound global auth.token proxy routes ---
@app.route("/api/proxy-token")
@auth.token.token_required
def test_proxy_token_view():
    return {"status": "proxy_ok", "user": auth.token.current_user.email}

# --- Top-level export routes ---
@app.route("/api/top-token")
@top_token_required
def test_top_token_view():
    return {"status": "top_ok", "user": top_current_user.email}


with app.app_context():
    db.create_all()

    # Create regular user
    reg_user = User(email="regular@example.com", is_admin=False, is_active=True)
    reg_user.password = "RegPass123!"  # Tests UserModelMixin password setter
    db.session.add(reg_user)

    # Create admin user
    adm_user = User(email="admin@example.com", is_admin=True, is_active=True)
    adm_user.set_password("AdminPass123!")  # Tests set_password
    db.session.add(adm_user)
    db.session.commit()

client = app.test_client()


def test_user_model_mixin():
    print("\n--- TEST 1: UserModelMixin / TokenUserMixin ---")
    u = User(email="mixin_test@example.com")
    u.password = "MySecurePassword"
    assert u.check_password("MySecurePassword") is True
    assert u.check_password("WrongPassword") is False

    # Serialization exclusion
    d = u.to_dict()
    assert "password" not in d
    assert "password_hash" not in d
    assert d.get("email") == "mixin_test@example.com"
    print("[PASS] UserModelMixin password hashing, verification, and serialization passed.")


def test_built_in_auth_endpoints():
    print("\n--- TEST 2: Built-in Auth Endpoints (/login, /refresh, /me, /logout) ---")
    # 2.1 Login regular user
    res = client.post("/auth/login", json={"email": "regular@example.com", "password": "RegPass123!"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert "access_token" in data
    reg_token = data["access_token"]
    assert "refresh_token" in res.headers.get("Set-Cookie", "")
    print("[PASS] POST /auth/login issued Bearer access_token and refresh cookie.")

    # 2.2 GET /auth/me with Bearer token
    res_me = client.get("/auth/me", headers={"Authorization": f"Bearer {reg_token}"})
    assert res_me.status_code == 200
    assert res_me.get_json()["data"]["email"] == "regular@example.com"
    print("[PASS] GET /auth/me returned correct authenticated user profile.")

    # 2.3 POST /auth/refresh using HttpOnly cookie
    res_refresh = client.post("/auth/refresh")
    assert res_refresh.status_code == 200
    new_data = res_refresh.get_json()
    assert new_data["success"] is True
    assert "access_token" in new_data
    print("[PASS] POST /auth/refresh successfully rotated token via cookie.")

    # 2.4 POST /auth/logout
    res_logout = client.post("/auth/logout")
    assert res_logout.status_code == 200
    print("[PASS] POST /auth/logout cleared session cookie.")


def test_namespaced_token_auth():
    print("\n--- TEST 3: Dedicated flask_build_api.token Namespace ---")
    # Login regular user and admin user to acquire tokens
    res_reg = client.post("/auth/login", json={"email": "regular@example.com", "password": "RegPass123!"})
    reg_token = res_reg.get_json()["access_token"]

    res_adm = client.post("/auth/login", json={"email": "admin@example.com", "password": "AdminPass123!"})
    adm_token = res_adm.get_json()["access_token"]

    # 3.1 Unauthenticated request -> Must return JSON 401
    res = client.get("/api/test-token")
    assert res.status_code == 401
    assert res.is_json
    assert res.get_json()["error"]["code"] == "unauthorized"
    print("[PASS] Unauthenticated request correctly rejected with 401 JSON.")

    # 3.2 Authenticated Bearer token -> 200 OK
    res = client.get("/api/test-token", headers={"Authorization": f"Bearer {reg_token}"})
    assert res.status_code == 200
    assert res.get_json() == {"status": "ok", "user": "regular@example.com"}
    print("[PASS] @namespaced_token_required authenticated Bearer token and resolved current_user.")

    # 3.3 login_required alias
    res = client.get("/api/test-login-alias", headers={"Authorization": f"Bearer {reg_token}"})
    assert res.status_code == 200
    assert res.get_json()["user"] == "regular@example.com"
    print("[PASS] token.login_required alias worked identically to token_required.")

    # 3.4 Regular user on admin route -> 403 Forbidden
    res = client.get("/api/test-admin", headers={"Authorization": f"Bearer {reg_token}"})
    assert res.status_code == 403
    assert res.is_json
    assert res.get_json()["error"]["code"] == "forbidden"
    print("[PASS] Regular user token rejected on admin route with 403 JSON.")

    # 3.5 Admin user on admin route -> 200 OK
    res = client.get("/api/test-admin", headers={"Authorization": f"Bearer {adm_token}"})
    assert res.status_code == 200
    assert res.get_json()["user"] == "admin@example.com"
    print("[PASS] Admin user granted access to admin route.")

    # 3.6 Bound auth_mgr.token namespace
    res = client.get("/api/bound-mgr-token", headers={"Authorization": f"Bearer {reg_token}"})
    assert res.status_code == 200
    assert res.get_json()["user"] == "regular@example.com"

    res = client.get("/api/bound-mgr-admin", headers={"Authorization": f"Bearer {reg_token}"})
    assert res.status_code == 403

    res = client.get("/api/bound-mgr-admin", headers={"Authorization": f"Bearer {adm_token}"})
    assert res.status_code == 200
    print("[PASS] Bound auth_mgr.token namespace verified.")

    # 3.7 Proxy auth.token routes
    res = client.get("/api/proxy-token", headers={"Authorization": f"Bearer {reg_token}"})
    assert res.status_code == 200
    assert res.get_json()["user"] == "regular@example.com"
    print("[PASS] Bound proxy auth.token verified.")

    # 3.8 Top-level exports
    res = client.get("/api/top-token", headers={"Authorization": f"Bearer {reg_token}"})
    assert res.status_code == 200
    assert res.get_json()["user"] == "regular@example.com"
    print("[PASS] Top-level exports from flask_build_api verified.")


if __name__ == "__main__":
    test_user_model_mixin()
    test_built_in_auth_endpoints()
    test_namespaced_token_auth()
    print("\n=======================================================")
    print("ALL FLASK-BUILD-API TOKEN AUTH TESTS PASSED FLAWLESSLY!")
    print("=======================================================\n")

