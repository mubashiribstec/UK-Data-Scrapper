"""SerpAPI search enricher — a paid, reliable Google-search fallback.

Used only when the free DuckDuckGo enricher returns nothing or has been
disabled by its circuit breaker (common on datacenter/VPS IPs). SerpAPI
returns structured JSON, so the search step needs no HTML scraping; the
top result URLs are still deep-scraped with the website enricher for
phone/email contacts, mirroring enrichers/duckduckgo.py.
"""

import threading
import logging
from typing import Optional
from datetime import datetime

import requests

from processing.cleaner import extract_phones, extract_emails
from processing.merger import ContactRecord
from enrichers.website import enrich_from_website

logger = logging.getLogger(__name__)

SERPAPI_URL = "https://serpapi.com/search.json"

# Circuit breaker: a bad/exhausted key would otherwise fail on every company
# in the batch. After _MAX_CONSECUTIVE_FAILURES hard failures in a row,
# disable SerpAPI for the rest of the run.
_MAX_CONSECUTIVE_FAILURES = 3
_lock = threading.Lock()
_consecutive_failures = 0
_disabled = False


def _record_failure():
    global _consecutive_failures, _disabled
    with _lock:
        _consecutive_failures += 1
        if _consecutive_failures >= _MAX_CONSECUTIVE_FAILURES and not _disabled:
            _disabled = True
            logger.warning(
                f"SerpAPI: {_consecutive_failures} consecutive failures — "
                "disabling for the rest of this run"
            )


def _record_success():
    global _consecutive_failures
    with _lock:
        _consecutive_failures = 0


def is_serpapi_disabled() -> bool:
    with _lock:
        return _disabled


def enrich_from_serpapi(
    company: str,
    location: Optional[str] = None,
    timeout: int = 10,
    api_key: str = "",
) -> Optional[ContactRecord]:
    if not api_key or is_serpapi_disabled():
        return None

    record = ContactRecord(company=company)
    record.enrichment_sources = ["serpapi"]

    location_hint = f" {location}" if location else " UK"
    query = f'"{company}" contact phone email{location_hint}'

    # SerpAPI's own round trip (search + Google backend) regularly needs more
    # than the generic enrichment_timeout (10s default), which produced
    # frequent read-timeouts. Give it a sturdier floor independent of that.
    search_timeout = max(timeout, 15)

    try:
        resp = requests.get(
            SERPAPI_URL,
            params={
                "engine": "google",
                "q": query,
                "api_key": api_key,
                "gl": "uk",
                "hl": "en",
            },
            timeout=search_timeout,
        )
        if resp.status_code == 401:
            logger.warning("SerpAPI: API key invalid or unauthorised (HTTP 401)")
            _record_failure()
            return None
        if resp.status_code == 429:
            logger.warning("SerpAPI: rate limit / quota exhausted (HTTP 429)")
            _record_failure()
            return None
        if resp.status_code != 200:
            logger.debug(f"SerpAPI returned {resp.status_code} for '{company}'")
            _record_failure()
            return None

        data = resp.json()
        if data.get("error"):
            logger.warning(f"SerpAPI error for '{company}': {data['error']}")
            _record_failure()
            return None

        # A parseable response means the network path / key work — reset breaker.
        _record_success()

        organic = data.get("organic_results") or []

        all_phones = []
        all_emails = []
        result_urls = []

        for result in organic:
            snippet = result.get("snippet", "") or ""
            if snippet:
                all_phones.extend(extract_phones(snippet))
                all_emails.extend(extract_emails(snippet))
            link = result.get("link", "")
            if link.startswith("http"):
                result_urls.append(link)

        # Deep-scrape top 2 result URLs with the website enricher
        for url in result_urls[:2]:
            try:
                site_result = enrich_from_website(company, company_url=url, timeout=8)
                if site_result:
                    all_phones.extend(site_result.phone_numbers)
                    all_emails.extend(site_result.emails)
                    if not record.company_website:
                        record.company_website = url
            except Exception as e:
                logger.debug(f"SerpAPI URL scrape failed for {url}: {e}")

        # Deduplicate
        seen_p, seen_e = set(), set()
        for p in all_phones:
            if p not in seen_p:
                seen_p.add(p)
                record.phone_numbers.append(p)
        for e in all_emails:
            if e.lower() not in seen_e:
                seen_e.add(e.lower())
                record.emails.append(e)

        record.enriched_at = datetime.utcnow().isoformat() + "Z"

        if record.phone_numbers or record.emails:
            return record
        return None

    except Exception as e:
        logger.warning(f"SerpAPI enricher failed for '{company}': {e}")
        _record_failure()
        return None
