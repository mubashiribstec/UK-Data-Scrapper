import time
import threading
import logging
from typing import Optional
from datetime import datetime
import requests
from bs4 import BeautifulSoup

from processing.cleaner import extract_phones, extract_emails
from processing.merger import ContactRecord
from enrichers.website import enrich_from_website

logger = logging.getLogger(__name__)

DDG_URL = "https://html.duckduckgo.com/html/"
DDG_DELAY = 1.5
DDG_DOMAIN = "html.duckduckgo.com"

# Circuit breaker: when DuckDuckGo is blocked from this network (common on
# datacenter/VPS IPs), don't make every company in the batch pay the
# timeout/retry cost. After _MAX_CONSECUTIVE_FAILURES hard failures in a
# row, disable DDG for the rest of the run.
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
                f"DuckDuckGo: {_consecutive_failures} consecutive failures — "
                "disabling for the rest of this run"
            )


def _record_success():
    global _consecutive_failures
    with _lock:
        _consecutive_failures = 0


def is_duckduckgo_disabled() -> bool:
    with _lock:
        return _disabled


def enrich_from_duckduckgo(
    company: str,
    location: Optional[str] = None,
    timeout: int = 10,
) -> Optional[ContactRecord]:
    if is_duckduckgo_disabled():
        return None

    record = ContactRecord(company=company)
    record.enrichment_sources = ["duckduckgo"]

    location_hint = f" {location}" if location else " UK"
    query = f'"{company}" contact phone email{location_hint}'

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.9",
    })

    try:
        time.sleep(DDG_DELAY)
        resp = session.post(
            DDG_URL,
            data={"q": query, "b": "", "kl": "uk-en"},
            timeout=timeout,
        )
        if resp.status_code != 200:
            logger.debug(f"DuckDuckGo returned {resp.status_code} for '{company}'")
            _record_failure()
            return None

        soup = BeautifulSoup(resp.text, "lxml")

        # Check for block
        if not soup.find(class_="result__snippet") and not soup.find(class_="results"):
            logger.debug(f"DuckDuckGo: possible block for '{company}'")
            _record_failure()
            return None

        # A parseable results page (even with 0 contact matches) means the
        # network path to DDG works — reset the breaker.
        _record_success()

        all_phones = []
        all_emails = []
        result_urls = []

        snippets = soup.find_all(class_="result__snippet")
        for snippet in snippets:
            text = snippet.get_text(separator=" ")
            all_phones.extend(extract_phones(text))
            all_emails.extend(extract_emails(text))

        # Collect top 2 result URLs for deeper scraping
        for a_tag in soup.select("a.result__url, .result__a")[:3]:
            href = a_tag.get("href", "")
            if href.startswith("http") and "duckduckgo.com" not in href:
                result_urls.append(href)

        # Scrape top 2 URLs with website enricher
        for url in result_urls[:2]:
            try:
                site_result = enrich_from_website(company, company_url=url, timeout=8)
                if site_result:
                    all_phones.extend(site_result.phone_numbers)
                    all_emails.extend(site_result.emails)
                    if not record.company_website:
                        record.company_website = url
            except Exception as e:
                logger.debug(f"DuckDuckGo URL scrape failed for {url}: {e}")

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
        logger.warning(f"DuckDuckGo enricher failed for '{company}': {e}")
        _record_failure()
        return None
