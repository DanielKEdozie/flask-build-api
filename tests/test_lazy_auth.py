import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
AUTH_MODULES = (
    "flask_build_api.api_auth",
    "flask_build_api.security",
    "flask_build_api.token",
    "flask_build_api.token_auth",
)


def _run_isolated(code: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(SRC) + (os.pathsep + existing if existing else "")
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )


def test_auth_modules_are_not_loaded_by_import():
    result = _run_isolated(
        f"""
import sys
import flask_build_api

names = {list(AUTH_MODULES)!r}
print(",".join(str(name in sys.modules) for name in names))
print(flask_build_api.__version__)
"""
    )
    loaded, version = result.stdout.strip().splitlines()
    assert loaded == "False,False,False,False"
    assert version


def test_lazy_auth_exports_resolve():
    result = _run_isolated(
        """
import flask_build_api
from flask_build_api import (
    ApiAuth,
    TokenAuthManager,
    UserModelMixin,
    auth,
    security,
    token,
)

print(ApiAuth.__name__)
print(TokenAuthManager.__name__)
print(UserModelMixin.__name__)
print(security.__name__)
print(token.__name__)
print(type(auth).__name__)
print(type(flask_build_api.auth).__name__)
"""
    )
    assert result.stdout.strip().splitlines() == [
        "ApiAuth",
        "TokenAuthManager",
        "UserModelMixin",
        "flask_build_api.security",
        "flask_build_api.token",
        "AuthProxy",
        "AuthProxy",
    ]
