"""flask_build_api.token
---------------------
Compatibility alias for flask_build_api.security.
"""

from .security import (
    TokenAuthManager,
    TokenUserMixin,
    UserModelMixin,
    admin_required,
    current_user,
    login_required,
    token_required,
)

__all__ = [
    "TokenAuthManager",
    "TokenUserMixin",
    "UserModelMixin",
    "admin_required",
    "current_user",
    "login_required",
    "token_required",
]
