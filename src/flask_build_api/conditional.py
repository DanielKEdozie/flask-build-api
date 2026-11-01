import hashlib
import json
from typing import Any, Optional


def generate_etag(data: Any) -> str:
    """Generate a stable MD5 or SHA1 ETag string for a serializable object or model."""
    if hasattr(data, "updated_at") and getattr(data, "updated_at") is not None:
        raw = f"{getattr(data, 'id', '')}:{getattr(data, 'updated_at')}"
    elif hasattr(data, "version") and getattr(data, "version") is not None:
        raw = f"{getattr(data, 'id', '')}:{getattr(data, 'version')}"
    elif isinstance(data, (dict, list)):
        raw = json.dumps(data, sort_keys=True, default=str)
    else:
        raw = str(data)

    digest = hashlib.md5(raw.encode("utf-8")).hexdigest()
    return f'"{digest}"'


def match_etag(header_value: Optional[str], current_etag: str) -> bool:
    """Check if client header (If-Match or If-None-Match) matches the current ETag."""
    if not header_value:
        return False
    tags = [t.strip() for t in header_value.split(",")]
    if "*" in tags:
        return True
    return current_etag in tags or current_etag.strip('"') in tags

