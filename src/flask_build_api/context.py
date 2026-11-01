from typing import Any, Dict, Optional
from flask import g, request


class ApiContext:
    """Request-scoped execution context providing access to the current action,
    resource, parent, authenticated user, database session, and arbitrary custom state.
    """

    def __init__(
        self,
        action: str,
        resource: Any,
        api: Optional[Any] = None,
        identifier: Optional[Any] = None,
        instance: Optional[Any] = None,
        parent_id: Optional[Any] = None,
        parent: Optional[Any] = None,
        request_id: Optional[str] = None,
        data: Optional[Any] = None,
        validated_data: Optional[Any] = None,
        **kwargs
    ):
        self.action = action
        self.resource = resource
        self.api = api or getattr(resource, "api", None)
        self.identifier = identifier
        self.instance = instance
        self.parent_id = parent_id
        self.parent = parent
        self.request_id = request_id
        self.data = data
        self.validated_data = validated_data if validated_data is not None else data
        self._state: Dict[str, Any] = dict(kwargs)

    @property
    def model(self) -> Any:
        return getattr(self.resource, "model", None)

    @property
    def session(self) -> Any:
        if hasattr(self.resource, "get_session"):
            return self.resource.get_session()
        return None

    @property
    def request(self) -> Any:
        return request

    @property
    def user(self) -> Optional[Any]:
        """Resolve authenticated user from api.auth, g, or flask-user-auth."""
        if self.api and self.api.auth and hasattr(self.api.auth, "get_current_user"):
            return self.api.auth.get_current_user()
        if hasattr(g, "current_user"):
            return g.current_user
        try:
            from flask_user_auth import current_user as u_user
            return u_user
        except ImportError:
            return None

    @property
    def current_user(self) -> Optional[Any]:
        return self.user

    def get(self, key: str, default: Any = None) -> Any:
        return self._state.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._state[key] = value

    def __getitem__(self, item: str) -> Any:
        return self._state[item]

    def __setitem__(self, key: str, value: Any) -> None:
        self._state[key] = value

    def __contains__(self, item: str) -> bool:
        return item in self._state

    def __repr__(self) -> str:
        res_name = getattr(self.resource, "name", "unknown")
        return f"<ApiContext action='{self.action}' resource='{res_name}' parent_id={self.parent_id}>"

