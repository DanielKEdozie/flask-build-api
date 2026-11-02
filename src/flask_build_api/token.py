"""flask_build_api.token
---------------------
Dedicated token authentication namespace for flask-build-api powered by TokenAuthManager.

Usage::

    from flask_build_api.token import (
        token_required,
        login_required,
        admin_required,
        current_user,
        UserModelMixin,
    )

    @app.route('/api/profile')
    @token_required
    def profile():
        return {'email': current_user.email}

    @app.route('/api/admin/stats')
    @admin_required
    def stats():
        return {'status': 'ok'}
"""

from functools import wraps
from typing import Any, Callable, Optional

from flask import current_app, g, jsonify, request
from werkzeug.local import LocalProxy

from .token_auth import (
    TokenAuthManager,
    TokenUserMixin,
    UserModelMixin,
    get_token_auth_manager,
)


def _resolve_token_user() -> Optional[Any]:
    """Resolve currently authenticated token user from request context or Bearer header."""
    # 1. Check cached objects on g
    if hasattr(g, "_token_user") and g._token_user is not None:
        return g._token_user
    if hasattr(g, "current_user") and g.current_user is not None:
        return g.current_user
    if hasattr(g, "flask_httpauth_user") and g.flask_httpauth_user is not None:
        return g.flask_httpauth_user

    # 2. Extract Bearer token from header and verify if manager is active
    try:
        auth_header = request.headers.get("Authorization", "")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
            mgr = get_token_auth_manager()
            if mgr is not None:
                user = mgr.verify_token(token, expected_type="access")
                if user is not None:
                    g.current_user = user
                    g._token_user = user
                    g.auth_token = token
                    g.auth_method = "token"
                    return user
    except Exception:
        pass

    # 3. Check active manager's current_user property
    try:
        mgr = get_token_auth_manager()
        if mgr is not None and mgr.current_user is not None:
            return mgr.current_user
    except Exception:
        pass

    return None


#: Proxy strictly resolving to the token-authenticated user, or ``None``.
current_user = LocalProxy(_resolve_token_user)


def token_required(*args, admin: bool = False, **kwargs) -> Callable:
    """Decorator requiring valid Bearer JWT token authentication.

    Always returns JSON error responses on failure (never redirects).

    Supports::

        @token_required
        @token_required()
        @token_required(admin=True)
    """
    direct_fn = args[0] if (len(args) == 1 and callable(args[0]) and not kwargs) else None

    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*fn_args, **fn_kwargs):
            mgr = get_token_auth_manager()

            # 1. If TokenAuthManager is registered, delegate directly
            if mgr is not None:
                return mgr.token_required(admin=admin, **kwargs)(fn)(*fn_args, **fn_kwargs)

            # 2. Standalone fallback verification
            auth_header = request.headers.get("Authorization", "")
            if not auth_header or not auth_header.startswith("Bearer "):
                return jsonify({
                    "success": False,
                    "error": {
                        "code": "unauthorized",
                        "message": "Missing or invalid Bearer token",
                    }
                }), 401

            user = _resolve_token_user()
            if user is None:
                err_msg = getattr(g, "_token_error_message", "Invalid or expired token")
                return jsonify({
                    "success": False,
                    "error": {
                        "code": "unauthorized",
                        "message": err_msg,
                    }
                }), 401

            if not getattr(user, "is_active", True):
                return jsonify({
                    "success": False,
                    "error": {
                        "code": "forbidden",
                        "message": "Account is inactive",
                    }
                }), 403

            if admin and not (
                getattr(user, "is_admin", False)
                or getattr(user, "is_superuser", False)
                or getattr(user, "is_staff", False)
            ):
                return jsonify({
                    "success": False,
                    "error": {
                        "code": "forbidden",
                        "message": "Administrator privileges required",
                    }
                }), 403

            g.current_user = user
            g._token_user = user
            g.auth_method = "token"
            return fn(*fn_args, **fn_kwargs)

        return wrapper

    if direct_fn is not None:
        return decorator(direct_fn)
    return decorator


#: Alias allowing `from flask_build_api.token import login_required`
login_required = token_required


def admin_required(*args, **kwargs) -> Callable:
    """Decorator requiring token authentication with admin privileges.

    Supports::

        @admin_required
        @admin_required()
    """
    return token_required(*args, admin=True, **kwargs)


__all__ = [
    "TokenAuthManager",
    "TokenUserMixin",
    "UserModelMixin",
    "admin_required",
    "current_user",
    "login_required",
    "token_required",
]

