"""Plugin registry and dynamic proxy hub for flask-build-api.

Provides a unified integration system where capability providers and adapters
(auth, storage, celery/tasks, payment, catalog, etc.) are registered via
`api.extend(...)` and accessed anywhere via top-level imports:

    from flask_build_api import auth, storage, tasks, catalog, payment
"""

from functools import wraps
from typing import Any, Callable, Dict, Optional, Union
from flask import current_app, g, request
from .errors import ForbiddenError, UnauthorizedError


class PluginRegistry:
    """Registry for capability providers and adapters injected into flask-build-api."""

    def __init__(self):
        self._plugins: Dict[str, Any] = {}

    def register(self, name: str, provider: Any) -> None:
        self._plugins[name.lower().strip()] = provider

    def get(self, name: str) -> Optional[Any]:
        return self._plugins.get(name.lower().strip())

    def has(self, name: str) -> bool:
        return name.lower().strip() in self._plugins

    def all(self) -> Dict[str, Any]:
        return dict(self._plugins)

    def remove(self, name: str) -> Optional[Any]:
        return self._plugins.pop(name.lower().strip(), None)


# Default global registry shared across module imports
default_registry = PluginRegistry()


def get_plugin(name: str) -> Optional[Any]:
    """Retrieve an active plugin by name from current_app or default_registry."""
    name_clean = name.lower().strip()
    try:
        if current_app:
            if hasattr(current_app, "extensions"):
                api_ext = current_app.extensions.get("build_api")
                if api_ext and hasattr(api_ext, "get_plugin"):
                    p = api_ext.get_plugin(name_clean)
                    if p is not None:
                        return p
                # Direct extension lookup fallback
                if name_clean in current_app.extensions:
                    return current_app.extensions[name_clean]
    except (RuntimeError, AttributeError):
        pass
    return default_registry.get(name_clean)


class PluginProxy:
    """Lazy proxy delegating attribute access and calls to an underlying plugin/adapter.
    
    This allows importing adapters at module level (`from flask_build_api import payment`)
    while deferring resolution until runtime when `api.extend(...)` has been configured.
    """

    def __init__(self, plugin_name: str, doc: Optional[str] = None):
        self._plugin_name = plugin_name.lower().strip()
        self.__doc__ = doc or f"Dynamic proxy for '{self._plugin_name}'."

    def _resolve(self) -> Any:
        provider = get_plugin(self._plugin_name)
        if provider is None:
            raise RuntimeError(
                f"No '{self._plugin_name}' plugin is registered with flask-build-api. "
                f"Register one using api.extend('{self._plugin_name}', your_provider)."
            )
        return provider

    def __getattr__(self, item: str) -> Any:
        return getattr(self._resolve(), item)

    def __call__(self, *args, **kwargs) -> Any:
        return self._resolve()(*args, **kwargs)

    def __getitem__(self, item: Any) -> Any:
        return self._resolve()[item]

    def __repr__(self) -> str:
        provider = get_plugin(self._plugin_name)
        status = "unregistered" if provider is None else f"registered: {repr(provider)}"
        return f"<PluginProxy '{self._plugin_name}' ({status})>"


class AuthProxy(PluginProxy):
    """Dynamic Auth proxy supporting lazy decorator execution and user resolution.

    Can be imported directly:
        from flask_build_api import auth

        @auth.admin_required
        def my_view(): ...

        user = auth.current_user
    """

    def __init__(self):
        super().__init__("auth", "Dynamic proxy for authentication provider.")

    @property
    def token(self):
        """Bound token authentication namespace."""
        from .token import token_required, admin_required, login_required, current_user
        class _ProxyToken:
            @property
            def current_user(subself):
                return current_user
            def token_required(subself, *args, **kwargs):
                return token_required(*args, **kwargs)
            def login_required(subself, *args, **kwargs):
                return login_required(*args, **kwargs)
            def admin_required(subself, *args, **kwargs):
                return admin_required(*args, **kwargs)
        return _ProxyToken()

    @property
    def current_user(self) -> Any:
        """Resolve current authenticated user from g, provider, or Flask-Login."""
        if hasattr(g, "current_user") and g.current_user is not None:
            return g.current_user

        provider = get_plugin("auth")
        if provider is not None:
            user = getattr(provider, "current_user", None)
            if callable(user):
                try:
                    res = user()
                    if res is not None:
                        return res
                except Exception:
                    pass
            elif user is not None:
                return user

            if hasattr(provider, "get_current_user") and callable(provider.get_current_user):
                try:
                    res = provider.get_current_user()
                    if res is not None:
                        return res
                except Exception:
                    pass

        try:
            from flask_login import current_user as flu_user
            if getattr(flu_user, "is_authenticated", False):
                return flu_user._get_current_object()
        except Exception:
            pass

        return None

    def login_user(self, user: Any, remember: bool = False, **kwargs) -> Any:
        """Log a user in via underlying provider or Flask-Login."""
        provider = get_plugin("auth")
        if provider is not None and hasattr(provider, "login_user") and callable(provider.login_user):
            return provider.login_user(user, remember=remember, **kwargs)
        from flask_login import login_user as flu_login_user
        return flu_login_user(user, remember=remember, **kwargs)

    def logout_user(self) -> Any:
        """Log the current user out via underlying provider or Flask-Login."""
        provider = get_plugin("auth")
        if provider is not None and hasattr(provider, "logout_user") and callable(provider.logout_user):
            return provider.logout_user()
        from flask_login import logout_user as flu_logout_user
        return flu_logout_user()

    def guard(
        self,
        target: Optional[Any] = None,
        *,
        admin: bool = False,
        redirect: Optional[str] = "/auth/login",
        token: Optional[bool] = None,
        session: Optional[bool] = None,
    ) -> Any:
        """Protect a Blueprint or view function with an authentication/authorization guard.

        Usage:
            # 1. Guard an entire Blueprint (registers before_request hook)
            auth.guard(admin_bp, admin=True, redirect='/auth/login')

            # 2. Guard as a decorator
            @auth.guard(admin=True, redirect='/auth/login')
            def my_view(): ...
        """
        def _apply_guard(obj: Any) -> Any:
            if hasattr(obj, "before_request") and not callable(obj):
                dec = (
                    self.admin_required(redirect=redirect)
                    if admin
                    else self.auth_required(redirect=redirect, token=token, session=session)
                )
                @obj.before_request
                @dec
                def _blueprint_auth_guard():
                    pass
                return obj

            if callable(obj):
                dec = (
                    self.admin_required(redirect=redirect)
                    if admin
                    else self.auth_required(redirect=redirect, token=token, session=session)
                )
                return dec(obj)

            return obj

        if target is not None:
            return _apply_guard(target)

        return _apply_guard

    def _invoke_decorator(
        self,
        method_name: str,
        f: Callable,
        call_args: tuple,
        kwargs: dict,
        fn_args: tuple,
        fn_kwargs: dict,
    ) -> Any:
        provider = self._resolve()
        if hasattr(provider, method_name):
            method = getattr(provider, method_name)
            if call_args or kwargs:
                dec = method(*call_args, **kwargs)
                return dec(f)(*fn_args, **fn_kwargs)
            else:
                try:
                    return method(f)(*fn_args, **fn_kwargs)
                except (TypeError, ValueError):
                    return method()(f)(*fn_args, **fn_kwargs)

        # Fallback if provider doesn't have the specific method name
        if method_name == "admin_required":
            if hasattr(provider, "auth_required"):
                return provider.auth_required(admin=True, *call_args, **kwargs)(f)(*fn_args, **fn_kwargs)
            user = self.current_user
            if user is None or not (getattr(user, "is_admin", False) or getattr(user, "is_staff", False)):
                raise ForbiddenError("Administrator access required")
            return f(*fn_args, **fn_kwargs)

        if method_name == "token_required":
            if hasattr(provider, "auth_required"):
                return provider.auth_required(token=True, session=False, *call_args, **kwargs)(f)(*fn_args, **fn_kwargs)
            if self.current_user is None:
                raise UnauthorizedError("Token authentication required")
            return f(*fn_args, **fn_kwargs)

        if self.current_user is None:
            raise UnauthorizedError("Authentication required")
        return f(*fn_args, **fn_kwargs)

    def auth_required(self, *args, **kwargs) -> Callable:
        """Decorator requiring authentication. Evaluates provider lazily at request time."""
        direct_fn = args[0] if (len(args) == 1 and callable(args[0]) and not kwargs) else None
        call_args = () if direct_fn else args

        def decorator(f: Callable) -> Callable:
            @wraps(f)
            def wrapper(*fn_args, **fn_kwargs):
                return self._invoke_decorator("auth_required", f, call_args, kwargs, fn_args, fn_kwargs)
            return wrapper

        if direct_fn is not None:
            return decorator(direct_fn)
        return decorator

    def requires_auth(self, *args, **kwargs) -> Callable:
        return self.auth_required(*args, **kwargs)

    def token_required(self, *args, **kwargs) -> Callable:
        """Decorator requiring token authentication."""
        direct_fn = args[0] if (len(args) == 1 and callable(args[0]) and not kwargs) else None
        call_args = () if direct_fn else args

        def decorator(f: Callable) -> Callable:
            @wraps(f)
            def wrapper(*fn_args, **fn_kwargs):
                return self._invoke_decorator("token_required", f, call_args, kwargs, fn_args, fn_kwargs)
            return wrapper

        if direct_fn is not None:
            return decorator(direct_fn)
        return decorator

    def admin_required(self, *args, **kwargs) -> Callable:
        """Decorator requiring administrative privileges."""
        direct_fn = args[0] if (len(args) == 1 and callable(args[0]) and not kwargs) else None
        call_args = () if direct_fn else args

        def decorator(f: Callable) -> Callable:
            @wraps(f)
            def wrapper(*fn_args, **fn_kwargs):
                return self._invoke_decorator("admin_required", f, call_args, kwargs, fn_args, fn_kwargs)
            return wrapper

        if direct_fn is not None:
            return decorator(direct_fn)
        return decorator

    def login_required(self, *args, **kwargs) -> Callable:
        """Decorator requiring session login."""
        direct_fn = args[0] if (len(args) == 1 and callable(args[0]) and not kwargs) else None
        call_args = () if direct_fn else args

        def decorator(f: Callable) -> Callable:
            @wraps(f)
            def wrapper(*fn_args, **fn_kwargs):
                return self._invoke_decorator("login_required", f, call_args, kwargs, fn_args, fn_kwargs)
            return wrapper

        if direct_fn is not None:
            return decorator(direct_fn)
        return decorator


class StorageProxy(PluginProxy):
    """Dynamic Storage proxy for file storage engines."""

    def __init__(self):
        super().__init__("storage", "Dynamic proxy for file storage engine.")


class TasksProxy(PluginProxy):
    """Dynamic Tasks proxy (e.g. CeleryAdapter or background queue)."""

    def __init__(self):
        super().__init__("tasks", "Dynamic proxy for background tasks engine (e.g. Celery).")


class CatalogProxy(PluginProxy):
    """Dynamic Catalog proxy (e.g. FacebookCatalogAdapter or Google Merchant)."""

    def __init__(self):
        super().__init__("catalog", "Dynamic proxy for catalog syndication engine.")


class PaymentProxy(PluginProxy):
    """Dynamic Payment proxy (e.g. PaystackAdapter or StripeAdapter)."""

    def __init__(self):
        super().__init__("payment", "Dynamic proxy for payment gateway engine.")


# Singleton instances ready for top-level import
auth = AuthProxy()
storage = StorageProxy()
tasks = TasksProxy()
catalog = CatalogProxy()
payment = PaymentProxy()
