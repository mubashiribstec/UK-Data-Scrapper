import logging
import random

logger = logging.getLogger(__name__)

# Optional free proxy rotation — disabled by default.
# Set USE_PROXIES=true in .env and populate PROXY_LIST with newline-separated proxies.

_proxy_list: list = []


def load_proxies(proxy_file: str = "./proxies.txt") -> list:
    global _proxy_list
    try:
        with open(proxy_file) as f:
            _proxy_list = [line.strip() for line in f if line.strip()]
        logger.info(f"Loaded {len(_proxy_list)} proxies from {proxy_file}")
    except FileNotFoundError:
        logger.debug("No proxy file found, running without proxies")
    return _proxy_list


def get_proxy() -> dict | None:
    if not _proxy_list:
        return None
    proxy = random.choice(_proxy_list)
    return {"http": proxy, "https": proxy}
