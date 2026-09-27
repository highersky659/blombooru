from threading import Lock
from typing import Dict, Optional, Tuple
from urllib.parse import urlparse

from sqlalchemy import func
from sqlalchemy.orm import Session

from ...models import BooruConfig
from ...utils.logger import logger
from .base import BooruClient
from .danbooru import DanbooruClient
from .gelbooru import GelbooruClient

_CLIENT_CLASSES = [
    DanbooruClient,
    GelbooruClient,
]

_client_cache: Dict[Tuple, BooruClient] = {}
_cache_lock = Lock()

def get_booru_config_for_url(db: Optional[Session], url: str) -> Optional[BooruConfig]:
    """Find matching BooruConfig for a given URL."""
    if not db or not url:
        return None
    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").strip().lower()
        if not hostname:
            return None

        candidates = [hostname]
        if parsed.port:
            candidates.append(f"{hostname}:{parsed.port}")
            if ":" in hostname:
                candidates.append(f"[{hostname}]:{parsed.port}")

        return (
            db.query(BooruConfig)
            .filter(func.lower(BooruConfig.domain).in_(candidates))
            .first()
        )
    except Exception as e:
        logger.debug(f"Failed to query BooruConfig for {url}: {e}")
        return None

def _get_cached_client(
    client_cls: type,
    base_url: str,
    username: Optional[str] = None,
    api_key: Optional[str] = None,
) -> BooruClient:
    """Retrieve or instantiate a cached BooruClient instance safely."""
    cache_key = (client_cls, base_url, username, api_key)
    with _cache_lock:
        client = _client_cache.get(cache_key)
        if client is None:
            if username and api_key:
                if client_cls == GelbooruClient:
                    client = client_cls(base_url, user_id=username, api_key=api_key)
                elif client_cls == DanbooruClient:
                    client = client_cls(base_url, username=username, api_key=api_key)
                else:
                    client = client_cls(base_url)
            else:
                client = client_cls(base_url)
            _client_cache[cache_key] = client
        return client

def get_client_for_url(
    url: str,
    db: Optional[Session] = None,
    config: Optional[BooruConfig] = None,
) -> Optional[BooruClient]:
    """Find the right BooruClient for a given URL by checking patterns."""
    for client_cls in _CLIENT_CLASSES:
        if client_cls.can_handle_url(url):
            parsed = urlparse(url)
            base_url = f"{parsed.scheme}://{parsed.netloc}"
            username: Optional[str] = None
            api_key: Optional[str] = None

            # Inject credentials only if both username and api_key are present
            if config is None and db:
                config = get_booru_config_for_url(db, url)
            if config and config.username and config.api_key:
                username = config.username
                api_key = config.api_key

            return _get_cached_client(client_cls, base_url, username, api_key)
    return None

def get_user_agent_for_url(url: str, db: Optional[Session] = None) -> str:
    """Get the appropriate User-Agent string for a given URL based on booru client config."""
    if not url:
        return "Blombooru/1.0 (booru-import)"

    config = get_booru_config_for_url(db, url) if db else None

    # If the URL directly matches a booru post pattern, use that client directly
    client = get_client_for_url(url, db=db, config=config)
    if client:
        if isinstance(client, DanbooruClient):
            client.ensure_user_id()
        if hasattr(client, "session"):
            return client.session.headers.get("User-Agent", "Blombooru/1.0 (booru-import)")

    # For asset/CDN URLs that don't match post patterns, resolve configured domain
    if config and config.username and config.api_key:
        parsed = urlparse(url)
        scheme = parsed.scheme if parsed.scheme in ("http", "https") else "https"
        base_url = f"{scheme}://{config.domain}"
        client = _get_cached_client(DanbooruClient, base_url, username=config.username, api_key=config.api_key)
        client.ensure_user_id()
        return client.session.headers.get("User-Agent", "Blombooru/1.0 (booru-import)")

    return "Blombooru/1.0 (booru-import)"

def clear_client_cache():
    """Clear cached booru client instances."""
    with _cache_lock:
        _client_cache.clear()
