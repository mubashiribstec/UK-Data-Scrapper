import logging
import random

logger = logging.getLogger(__name__)

# Optional proxy rotation — disabled unless a proxies file is loaded.
# Applies to requests-based scrapers (NHS, Reed). Playwright scrapers are
# excluded: rotating proxies conflicts with the persistent Indeed login profile.

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
