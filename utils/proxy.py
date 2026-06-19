import logging
import random
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Two separate proxy paths:
#  - The functions below rotate proxies from a file across the requests-based
#    scrapers (Reed API), disabled unless a proxies file is loaded.
#  - The Playwright/Indeed browser is proxied separately via
#    `playwright_proxy_dict`, applied once at browser launch (a single
#    auto-rotating residential gateway, so it doesn't conflict with the
#    persistent Indeed profile).

_proxy_list: list = []


def load_proxies(proxy_file: str = "./proxies.txt") -> list:
    global _proxy_list
    try:
        with open(proxy_file) as f:
            _proxy_list = [line.strip() for line in f if line.strip() and not line.startswith("#")]
        logger.info(f"Loaded {len(_proxy_list)} proxies from {proxy_file}")
    except FileNotFoundError:
        logger.warning(f"Proxy file not found: {proxy_file} — running without proxies")
    return _proxy_list


def get_proxy() -> dict | None:
    if not _proxy_list:
        return None
    proxy = random.choice(_proxy_list)
    return {"http": proxy, "https": proxy}


def apply_proxy(session) -> None:
    """Attach a random proxy to a requests.Session (no-op when none loaded)."""
    proxy = get_proxy()
    if proxy:
        session.proxies.update(proxy)
        logger.debug(f"Session proxy set: {proxy['http']}")


def rotate_proxy(session) -> None:
    """Swap the session's proxy for a different random one (e.g. after a 403)."""
    if not _proxy_list:
        return
    session.proxies.clear()
    apply_proxy(session)


def playwright_proxy_dict(url: str) -> dict | None:
    """Parse a proxy URL (scheme://user:pass@host:port) into Playwright's
    proxy shape: {"server": "scheme://host:port", "username", "password"}.

    Returns None for empty/blank input so it can be passed straight to
    Playwright's `proxy=` arg (which treats None as "no proxy").
    """
    if not url or not url.strip():
        return None
    parsed = urlparse(url.strip())
    if not parsed.hostname:
        logger.warning(f"Ignoring malformed PLAYWRIGHT_PROXY (no host): {url}")
        return None
    scheme = parsed.scheme or "http"
    server = f"{scheme}://{parsed.hostname}"
    if parsed.port:
        server += f":{parsed.port}"
    proxy = {"server": server}
    if parsed.username:
        proxy["username"] = parsed.username
    if parsed.password:
        proxy["password"] = parsed.password
    return proxy
