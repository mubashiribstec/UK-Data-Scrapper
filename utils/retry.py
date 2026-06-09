import time
import random
import logging
import functools

import requests

logger = logging.getLogger(__name__)

RETRY_STATUSES = (429, 500, 502, 503)


def retry(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    exceptions: tuple = (requests.RequestException,),
    retry_statuses: tuple = RETRY_STATUSES,
):
    """Exponential backoff with full jitter.

    Retries on the given exception types, and — when the wrapped function
    returns a requests.Response — on retryable HTTP statuses, honouring the
    Retry-After header when present. The last response/exception is
    returned/raised after the final attempt.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            result = None
            for attempt in range(max_attempts):
                try:
                    result = func(*args, **kwargs)
                    last_exc = None
                except exceptions as e:
                    last_exc = e
                    result = None

                if last_exc is None:
                    if not isinstance(result, requests.Response):
                        return result
                    if result.status_code not in retry_statuses:
                        return result

                if attempt == max_attempts - 1:
                    break

                delay = random.uniform(0, min(max_delay, base_delay * (2 ** attempt)))
                if isinstance(result, requests.Response):
                    retry_after = result.headers.get("Retry-After")
                    if retry_after:
                        try:
                            delay = min(max_delay, float(retry_after))
                        except ValueError:
                            pass
                    reason = f"HTTP {result.status_code}"
                else:
                    reason = str(last_exc)
                logger.warning(
                    f"{func.__name__}: attempt {attempt + 1}/{max_attempts} failed "
                    f"({reason}), retrying in {delay:.1f}s"
                )
                time.sleep(delay)

            if last_exc is not None:
                raise last_exc
            return result
        return wrapper
    return decorator
