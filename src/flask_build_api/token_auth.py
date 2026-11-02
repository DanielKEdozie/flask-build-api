"""Built-in Token and HttpOnly Cookie Refresh Authentication Manager for flask-build-api.

Provides native JWT authentication, Flask-HTTPAuth token verification,
and secure HttpOnly cookie refresh token rotation without requiring
external authentication extensions.
"""

from datetime import datetime, timedelta, timezone
from functools import wraps
import time
from typing import Any, Callable, Dict, Optional, Tuple, Type, Union
import uuid

from flask import Blueprint, Flask, current_app, g, jsonify, make_response, request
from werkzeug.security import check_password_hash, generate_password_hash

try:
    import jwt
except ImportError:
    jwt = None

try:
    from flask_httpauth import HTTPTokenAuth
except ImportError:
    HTTPTokenAuth = None

from .errors import ApiError, ForbiddenError, UnauthorizedError


class UserModelMixin:
    """Declarative user mixin for SQLAlchemy models used with TokenAuthManager.

    Provides:
    - password setter and set_password() method with automatic hashing
    - check_password() method for verifying raw passwords
    - is_active boolean (default True)
    - is_admin boolean (default False)
    - __serialize_exclude__ tuple omitting password hashes from API dumps
    - to_dict() serialization helper
    """
    __password_field__: str = "password_hash"
    __serialize_exclude__: Tuple[str, ...] = ("password", "password_hash")

    @property
    def password(self) -> str:
        raise AttributeError("password is a write-only attribute")

    @password.setter
    def password(self, raw_password: str) -> None:
        self.set_password(raw_password)

    def set_password(self, raw_password: str) -> None:
        """Hash and store the password in the configured password field."""
        if not raw_password:
            raise ValueError("Password cannot be empty")
        field = getattr(self, "__password_field__", "password_hash")
        setattr(self, field, generate_password_hash(raw_password))

    def check_password(self, raw_password: str) -> bool:
        """Verify password against stored hash."""
        field = getattr(self, "__password_field__", "password_hash")
        hashed = getattr(self, field, None)
        if not hashed:
            return False
        return check_password_hash(hashed, raw_password)

    def to_dict(self, include=None, exclude=None) -> Dict[str, Any]:
        """Convert model instance attributes to a dictionary, omitting excluded fields."""
        data: Dict[str, Any] = {}
        excluded = set(self.__serialize_exclude__)
        if exclude:
            excluded.update(exclude)

        if hasattr(self, "__table__") and hasattr(self.__table__, "columns"):
            for col in self.__table__.columns:
                if col.name not in excluded:
                    data[col.name] = getattr(self, col.name, None)
        else:
            for key, val in vars(self).items():
                if not key.startswith("_") and key not in excluded:
                    data[key] = val

        if include:
            for field in include:
                if hasattr(self, field):
                    data[field] = getattr(self, field)

        return data


#: Shorthand alias for UserModelMixin
TokenUserMixin = UserModelMixin


def get_token_auth_manager(app=None) -> Optional["TokenAuthManager"]:
    """Resolve the active TokenAuthManager instance from the current app or plugin registry."""
    app_obj = app or current_app
    try:
        if app_obj and hasattr(app_obj, "extensions"):
            mgr = (
                app_obj.extensions.get("token_auth")
                or app_obj.extensions.get("auth")
            )
            if isinstance(mgr, TokenAuthManager):
                return mgr

            # Check build_api extension
            api_ext = app_obj.extensions.get("build_api")
            if api_ext is not None:
                if hasattr(api_ext, "auth") and isinstance(api_ext.auth, TokenAuthManager):
                    return api_ext.auth
                if hasattr(api_ext, "get_plugin"):
                    p = api_ext.get_plugin("auth")
                    if isinstance(p, TokenAuthManager):
                        return p
    except (RuntimeError, AttributeError):
        pass

    try:
        from .plugins import get_plugin
        p = get_plugin("auth")
        if isinstance(p, TokenAuthManager):
            return p
    except Exception:
        pass

    return None


class TokenAuthManager:
    """Native JWT + Flask-HTTPAuth Token Authentication Manager.

    Features:
    - Short-lived Bearer access tokens for API requests
    - Secure HttpOnly, SameSite, Secure refresh token cookies (resistant to XSS)
    - Automatic token generation, decoding, and verification
    - Flask-HTTPAuth decorators (@auth.token_required, @auth.admin_required)
    - Standard auth endpoints (/login, /refresh, /me, /logout)
    """

    def __init__(
        self,
        model: Any,
        identity_field: str = "email",
        password_field: str = "password_hash",
        schema: Optional[Any] = None,
        secret_key: Optional[str] = None,
        algorithm: str = "HS256",
        access_expires_in: int = 900,         # 15 minutes
        refresh_expires_in: int = 2592000,    # 30 days
        refresh_cookie_name: str = "refresh_token",
        refresh_cookie_path: str = "/",
        refresh_cookie_samesite: str = "Lax",
        refresh_cookie_secure: Optional[bool] = None,
        refresh_cookie_httponly: bool = True,
        auto_mount: bool = True,
        url_prefix: str = "/auth",
    ):
        if jwt is None:
            raise ImportError(
                "PyJWT is required for TokenAuthManager. Install it with: pip install pyjwt"
            )

        self.model = model
        self.identity_field = identity_field
        self.password_field = password_field
        self.schema = schema
        self._secret_key = secret_key
        self.algorithm = algorithm
        self.access_expires_in = access_expires_in
        self.refresh_expires_in = refresh_expires_in
        self.refresh_cookie_name = refresh_cookie_name
        self.refresh_cookie_path = refresh_cookie_path
        self.refresh_cookie_samesite = refresh_cookie_samesite
        self._refresh_cookie_secure = refresh_cookie_secure
        self.refresh_cookie_httponly = refresh_cookie_httponly
        self.auto_mount = auto_mount
        self.url_prefix = url_prefix.rstrip("/")
        self._mounted = False

        # Initialize Flask-HTTPAuth if available
        if HTTPTokenAuth is not None:
            self.http_auth = HTTPTokenAuth(scheme="Bearer")
            self.http_auth.verify_token(self._verify_token_callback)
            self.http_auth.error_handler(self._http_auth_error_handler)
        else:
            self.http_auth = None

    @property
    def secret_key(self) -> str:
        if self._secret_key:
            return self._secret_key
        try:
            cfg = current_app.config
            key = cfg.get("JWT_SECRET_KEY") or cfg.get("SECRET_KEY")
            if key:
                return str(key)
        except RuntimeError:
            pass
        return "flask-build-api-fallback-secret-key-please-change-in-production"

    @property
    def is_cookie_secure(self) -> bool:
        if self._refresh_cookie_secure is not None:
            return self._refresh_cookie_secure
        try:
            return not current_app.debug and not current_app.testing
        except RuntimeError:
            return False

    def _get_db_session(self) -> Any:
        try:
            return current_app.extensions["sqlalchemy"].session
        except (KeyError, RuntimeError):
            if hasattr(self.model, "query") and hasattr(self.model.query, "session"):
                return self.model.query.session
        raise RuntimeError("Could not resolve SQLAlchemy session from application.")

    # --- Token Generation & Verification ------------------------------------

    def generate_token(self, user: Any, token_type: str = "access", expires_in: Optional[int] = None) -> str:
        """Create a signed JWT token for the user."""
        lifetime = expires_in if expires_in is not None else (
            self.access_expires_in if token_type == "access" else self.refresh_expires_in
        )
        now = datetime.now(timezone.utc)
        payload = {
            "sub": str(getattr(user, "id")),
            "type": token_type,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=lifetime)).timestamp()),
            "jti": str(uuid.uuid4()),
        }
        return jwt.encode(payload, self.secret_key, algorithm=self.algorithm)

    def generate_tokens(self, user: Any) -> Tuple[str, str]:
        """Generate (access_token, refresh_token) tuple for the user."""
        access_token = self.generate_token(user, token_type="access")
        refresh_token = self.generate_token(user, token_type="refresh")
        return access_token, refresh_token

    def verify_token(self, token: str, expected_type: str = "access") -> Optional[Any]:
        """Decode and verify token, returning user instance if valid."""
        try:
            payload = jwt.decode(token, self.secret_key, algorithms=[self.algorithm])
            if payload.get("type") != expected_type:
                g._token_error_message = f"Invalid token type (expected {expected_type})"
                return None

            user_id = payload.get("sub")
            if not user_id:
                g._token_error_message = "Invalid token subject"
                return None

            session = self._get_db_session()
            user = session.get(self.model, int(user_id) if str(user_id).isdigit() else user_id)
            if user is None:
                g._token_error_message = "User not found"
                return None

            if not getattr(user, "is_active", True):
                g._token_error_message = "User account is inactive"
                return None

            return user
        except jwt.ExpiredSignatureError:
            g._token_error_message = "Token has expired"
            return None
        except jwt.PyJWTError as e:
            g._token_error_message = f"Invalid token: {str(e)}"
            return None

    def _verify_token_callback(self, token: str) -> Optional[Any]:
        user = self.verify_token(token, expected_type="access")
        if user is not None:
            g.current_user = user
            g.auth_token = token
            g.auth_method = "token"
        return user

    def _http_auth_error_handler(self, status: int) -> Tuple[Any, int]:
        msg = getattr(g, "_token_error_message", "Authentication required")
        return jsonify({
            "success": False,
            "error": {
                "code": "unauthorized" if status == 401 else "forbidden",
                "message": msg,
            }
        }), status

    # --- User Resolution & Helpers ------------------------------------------

    def get_user_by_identity(self, identity: str) -> Optional[Any]:
        session = self._get_db_session()
        col = getattr(self.model, self.identity_field, None)
        if col is None:
            return None
        return session.query(self.model).filter(col == identity).first()

    def check_user_password(self, user: Any, password: str) -> bool:
        # 1. Custom check_password method on model
        if hasattr(user, "check_password") and callable(user.check_password):
            return bool(user.check_password(password))

        # 2. Werkzeug hash check against password_field
        hashed = getattr(user, self.password_field, None)
        if hashed and isinstance(hashed, str):
            return check_password_hash(hashed, password)

        return False

    def dump_user(self, user: Any) -> Dict[str, Any]:
        if self.schema:
            schema_inst = self.schema() if isinstance(self.schema, type) else self.schema
            return schema_inst.dump(user)
        # Fallback dictionary
        data = {"id": getattr(user, "id", None)}
        for attr in ["email", "username", "first_name", "last_name", "is_admin"]:
            if hasattr(user, attr):
                data[attr] = getattr(user, attr)
        return data

    @property
    def current_user(self) -> Optional[Any]:
        if hasattr(g, "current_user") and g.current_user is not None:
            return g.current_user
        if self.http_auth is not None:
            user = self.http_auth.current_user()
            if user is not None:
                return user
        return None

    # --- Route Handlers & HttpOnly Refresh Mechanism ------------------------

    def handle_login(self) -> Any:
        """Authenticate user, return Bearer access_token, and set HttpOnly refresh_token cookie."""
        data = request.get_json(silent=True) or {}
        identity = data.get(self.identity_field) or data.get("identity") or data.get("email") or data.get("username")
        password = data.get("password")

        if not identity or not password:
            return jsonify({
                "success": False,
                "error": {
                    "code": "bad_request",
                    "message": f"'{self.identity_field}' and 'password' are required.",
                }
            }), 400

        user = self.get_user_by_identity(identity)
        if not user or not self.check_user_password(user, password):
            return jsonify({
                "success": False,
                "error": {
                    "code": "invalid_credentials",
                    "message": "Invalid email/username or password.",
                }
            }), 401

        if not getattr(user, "is_active", True):
            return jsonify({
                "success": False,
                "error": {
                    "code": "account_disabled",
                    "message": "This account is disabled.",
                }
            }), 403

        access_token, refresh_token = self.generate_tokens(user)
        user_data = self.dump_user(user)

        resp_data = {
            "success": True,
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": self.access_expires_in,
            "user": user_data,
        }
        resp = make_response(jsonify(resp_data), 200)

        # Set secure HttpOnly cookie for refresh token
        resp.set_cookie(
            key=self.refresh_cookie_name,
            value=refresh_token,
            max_age=self.refresh_expires_in,
            httponly=self.refresh_cookie_httponly,
            samesite=self.refresh_cookie_samesite,
            secure=self.is_cookie_secure,
            path=self.refresh_cookie_path,
        )
        return resp

    def handle_refresh(self) -> Any:
        """Validate HttpOnly refresh cookie (or payload token) and issue a fresh access token."""
        # 1. Read from HttpOnly cookie first (XSS-safe standard)
        token = request.cookies.get(self.refresh_cookie_name)

        # 2. Fallback to JSON payload or Authorization header for mobile/headless clients
        if not token:
            body = request.get_json(silent=True) or {}
            token = body.get("refresh_token")
        if not token:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header[7:].strip()

        if not token:
            return jsonify({
                "success": False,
                "error": {
                    "code": "missing_token",
                    "message": f"Missing refresh token in cookie '{self.refresh_cookie_name}' or request body.",
                }
            }), 401

        user = self.verify_token(token, expected_type="refresh")
        if not user:
            msg = getattr(g, "_token_error_message", "Invalid or expired refresh token.")
            return jsonify({
                "success": False,
                "error": {
                    "code": "invalid_token",
                    "message": msg,
                }
            }), 401

        # Issue fresh access token and rotate refresh token
        new_access_token, new_refresh_token = self.generate_tokens(user)

        resp_data = {
            "success": True,
            "access_token": new_access_token,
            "token_type": "Bearer",
            "expires_in": self.access_expires_in,
        }
        resp = make_response(jsonify(resp_data), 200)

        # Rotate HttpOnly cookie
        resp.set_cookie(
            key=self.refresh_cookie_name,
            value=new_refresh_token,
            max_age=self.refresh_expires_in,
            httponly=self.refresh_cookie_httponly,
            samesite=self.refresh_cookie_samesite,
            secure=self.is_cookie_secure,
            path=self.refresh_cookie_path,
        )
        return resp

    def handle_logout(self) -> Any:
        """Clear the HttpOnly refresh token cookie and invalidate session."""
        resp = make_response(jsonify({
            "success": True,
            "message": "Successfully logged out.",
        }), 200)

        # Invalidate HttpOnly cookie
        resp.set_cookie(
            key=self.refresh_cookie_name,
            value="",
            expires=0,
            httponly=self.refresh_cookie_httponly,
            samesite=self.refresh_cookie_samesite,
            secure=self.is_cookie_secure,
            path=self.refresh_cookie_path,
        )
        return resp

    def handle_me(self) -> Any:
        """Return the profile of the current authenticated user."""
        user = self.current_user
        if not user:
            return jsonify({
                "success": False,
                "error": {"code": "unauthorized", "message": "Authentication required."}
            }), 401

        return jsonify({
            "success": True,
            "data": self.dump_user(user),
        }), 200

    # --- Decorators ---------------------------------------------------------

    def token_required(self, *args, admin: bool = False, **kwargs) -> Callable:
        """Decorator enforcing Bearer token authentication."""
        direct_fn = args[0] if (len(args) == 1 and callable(args[0]) and not kwargs) else None

        def decorator(f: Callable) -> Callable:
            @wraps(f)
            def wrapper(*fn_args, **fn_kwargs):
                # 1. Use Flask-HTTPAuth if available
                if self.http_auth is not None:
                    # Execute http_auth verification
                    auth_header = request.headers.get("Authorization", "")
                    if not auth_header or not auth_header.startswith("Bearer "):
                        return jsonify({
                            "success": False,
                            "error": {"code": "unauthorized", "message": "Missing or invalid Bearer token"}
                        }), 401

                    token = auth_header[7:].strip()
                    user = self._verify_token_callback(token)
                    if user is None:
                        msg = getattr(g, "_token_error_message", "Invalid or expired token")
                        return jsonify({
                            "success": False,
                            "error": {"code": "unauthorized", "message": msg}
                        }), 401

                    if admin and not (getattr(user, "is_admin", False) or getattr(user, "is_superuser", False)):
                        return jsonify({
                            "success": False,
                            "error": {"code": "forbidden", "message": "Administrator privileges required"}
                        }), 403

                    return f(*fn_args, **fn_kwargs)

                # Fallback verification
                auth_header = request.headers.get("Authorization", "")
                if not auth_header or not auth_header.startswith("Bearer "):
                    raise UnauthorizedError("Missing or invalid Bearer token")

                token = auth_header[7:].strip()
                user = self.verify_token(token, expected_type="access")
                if user is None:
                    msg = getattr(g, "_token_error_message", "Invalid or expired token")
                    raise UnauthorizedError(msg)

                if admin and not (getattr(user, "is_admin", False) or getattr(user, "is_superuser", False)):
                    raise ForbiddenError("Administrator privileges required")

                g.current_user = user
                return f(*fn_args, **fn_kwargs)

            return wrapper

        if direct_fn is not None:
            return decorator(direct_fn)
        return decorator

    def auth_required(self, *args, admin: bool = False, **kwargs) -> Callable:
        return self.token_required(*args, admin=admin, **kwargs)

    def login_required(self, *args, admin: bool = False, **kwargs) -> Callable:
        return self.token_required(*args, admin=admin, **kwargs)

    def admin_required(self, *args, **kwargs) -> Callable:
        return self.token_required(*args, admin=True, **kwargs)

    @property
    def token(self):
        """Bound token authentication namespace."""
        class _BoundToken:
            def __init__(subself, mgr):
                subself.mgr = mgr

            @property
            def current_user(subself):
                return subself.mgr.current_user

            def token_required(subself, *args, admin: bool = False, **kwargs):
                return subself.mgr.token_required(*args, admin=admin, **kwargs)

            def login_required(subself, *args, admin: bool = False, **kwargs):
                return subself.mgr.login_required(*args, admin=admin, **kwargs)

            def admin_required(subself, *args, **kwargs):
                return subself.mgr.admin_required(*args, **kwargs)

        return _BoundToken(self)

    @property
    def security(self):
        """Bound security & token authentication namespace (alias for .token)."""
        return self.token

    # --- Blueprint / App Mounting -------------------------------------------

    def init_app(self, app: Flask) -> "TokenAuthManager":
        """Initialize TokenAuthManager with a Flask application."""
        if not hasattr(app, "extensions"):
            app.extensions = {}
        app.extensions["token_auth"] = self
        app.extensions.setdefault("auth", self)
        if self.auto_mount:
            self.mount(app)
        return self

    def mount(self, app_or_blueprint: Union[Flask, Blueprint]) -> None:
        """Mount standard authentication routes (/login, /refresh, /me, /logout)."""
        if hasattr(app_or_blueprint, "extensions"):
            app_or_blueprint.extensions["token_auth"] = self
            app_or_blueprint.extensions.setdefault("auth", self)
        if self._mounted:
            return
        self._mounted = True
        prefix = self.url_prefix
        app_or_blueprint.add_url_rule(
            f"{prefix}/login",
            endpoint="auth_login",
            view_func=self.handle_login,
            methods=["POST"],
        )
        app_or_blueprint.add_url_rule(
            f"{prefix}/refresh",
            endpoint="auth_refresh",
            view_func=self.handle_refresh,
            methods=["POST"],
        )
        app_or_blueprint.add_url_rule(
            f"{prefix}/logout",
            endpoint="auth_logout",
            view_func=self.handle_logout,
            methods=["POST"],
        )
        app_or_blueprint.add_url_rule(
            f"{prefix}/me",
            endpoint="auth_me",
            view_func=self.token_required(self.handle_me),
            methods=["GET"],
        )


__all__ = [
    "TokenAuthManager",
    "TokenUserMixin",
    "UserModelMixin",
    "get_token_auth_manager",
]
