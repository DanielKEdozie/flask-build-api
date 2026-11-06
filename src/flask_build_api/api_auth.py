from functools import wraps
from typing import Any, Callable, List, Optional
from flask import current_app, g, request
from .errors import ForbiddenError, UnauthorizedError


class ApiAuth:
    """Authentication and authorization helper designed to integrate seamlessly
    with flask-user-auth or operate in standalone mode.
    """

    def __init__(self, backend: Optional[Any] = None, token: bool = True):
        self.backend = backend
        self.token = token

    def _resolve_backend(self) -> Optional[Any]:
        if self.backend is not None:
            return self.backend
        try:
            return (
                current_app.extensions.get("flask_user_auth")
                or current_app.extensions.get("auth")
                or current_app.extensions.get("user_auth")
            )
        except RuntimeError:
            return None

    def get_current_user(self) -> Optional[Any]:
        """Resolve currently authenticated user from g or flask-user-auth."""
        if hasattr(g, "current_user") and g.current_user is not None:
            return g.current_user
        try:
            from flask_user_auth import current_user as u_auth_user
            if u_auth_user and getattr(u_auth_user, "is_authenticated", True):
                return u_auth_user
        except ImportError:
            pass
        return None

    def requires_auth(self, refresh: bool = False, admin: bool = False) -> Callable:
        """Decorator to require a valid authenticated user via token (or session fallback)."""
        def decorator(f: Callable) -> Callable:
            @wraps(f)
            def wrapper(*args, **kwargs):
                backend = self._resolve_backend()

                # 1. Prefer token_required (strictly enforces Bearer JWT without session)
                if backend and hasattr(backend, "token_required"):
                    try:
                        return backend.token_required(admin=admin)(f)(*args, **kwargs)
                    except TypeError:
                        return backend.token_required(f)(*args, **kwargs)

                # 2. If backend has auth_required, delegate with token=True
                if backend and hasattr(backend, "auth_required"):
                    try:
                        return backend.auth_required(token=True, session=False, admin=admin, refresh=refresh)(f)(*args, **kwargs)
                    except TypeError:
                        try:
                            return backend.auth_required(token=True, session=False, admin=admin)(f)(*args, **kwargs)
                        except TypeError:
                            return backend.auth_required(f)(*args, **kwargs)

                # 3. Fallback / standalone token check
                auth_header = request.headers.get("Authorization", "")
                if not auth_header:
                    raise UnauthorizedError("Missing Authorization header")
                if not auth_header.startswith("Bearer "):
                    raise UnauthorizedError("Invalid token format")

                user = self.get_current_user()
                if user is None:
                    raise UnauthorizedError("Invalid or expired token")

                if admin and not (getattr(user, "is_admin", False) or getattr(user, "is_superuser", False)):
                    raise ForbiddenError("Administrator privileges required")

                return f(*args, **kwargs)

            return wrapper

        return decorator

    def token_required(self, admin: bool = False) -> Callable:
        """Require token authentication, optionally enforcing admin privileges."""
        return self.requires_auth(admin=admin)

    def admin_required(self) -> Callable:
        """Decorator to require admin privileges via token_required(admin=True)."""
        return self.requires_auth(admin=True)

    def roles_required(self, *roles: str) -> Callable:
        """Decorator to require one or more specific roles."""
        def decorator(f: Callable) -> Callable:
            @self.requires_auth()
            @wraps(f)
            def wrapper(*args, **kwargs):
                user = self.get_current_user()
                if not user:
                    raise UnauthorizedError("Authentication required")

                user_roles = getattr(user, "roles", [])
                user_role_names = [r.name if hasattr(r, "name") else str(r) for r in user_roles]

                if not any(r in user_role_names for r in roles):
                    raise ForbiddenError(f"Required role(s): {', '.join(roles)}")

                return f(*args, **kwargs)

            return wrapper

        return decorator

