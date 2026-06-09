import logging

logger = logging.getLogger(__name__)


def safe_scrape(func, *args, fallback=None, **kwargs):
    """Wrap any function call; log errors and return fallback instead of raising."""
    try:
        return func(*args, **kwargs)
    except Exception as e:
        logger.error(f"{func.__name__} failed: {e}")
        return fallback if fallback is not None else []
