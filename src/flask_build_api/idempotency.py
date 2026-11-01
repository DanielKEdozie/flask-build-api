import time
from typing import Any, Dict, Optional, Tuple


class IdempotencyStore:
    def get(self, key: str) -> Optional[Tuple[Any, int, Dict[str, str]]]:
        raise NotImplementedError

    def set(self, key: str, response: Tuple[Any, int, Dict[str, str]], ttl: int = 86400) -> None:
        raise NotImplementedError


class MemoryIdempotencyStore(IdempotencyStore):
    """In-memory cache for idempotency keys with TTL expiration."""

    def __init__(self):
        self._cache: Dict[str, Tuple[float, Tuple[Any, int, Dict[str, str]]]] = {}

    def get(self, key: str) -> Optional[Tuple[Any, int, Dict[str, str]]]:
        record = self._cache.get(key)
        if not record:
            return None
        expires_at, response = record
        if time.time() > expires_at:
            self._cache.pop(key, None)
            return None
        return response

    def set(self, key: str, response: Tuple[Any, int, Dict[str, str]], ttl: int = 86400) -> None:
        self._cache[key] = (time.time() + ttl, response)


class RedisIdempotencyStore(IdempotencyStore):
    """Redis-backed idempotency store for distributed deployments."""

    def __init__(self, redis_client: Any):
        self.redis = redis_client

    def get(self, key: str) -> Optional[Tuple[Any, int, Dict[str, str]]]:
        import json
        raw = self.redis.get(f"idempotency:{key}")
        if not raw:
            return None
        data = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
        return data["body"], data["status"], data.get("headers", {})

    def set(self, key: str, response: Tuple[Any, int, Dict[str, str]], ttl: int = 86400) -> None:
        import json
        body, status, headers = response
        payload = json.dumps({"body": body, "status": status, "headers": headers}, default=str)
        self.redis.setex(f"idempotency:{key}", ttl, payload)

