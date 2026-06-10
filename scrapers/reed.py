import re
import time
import logging
from datetime import datetime
from typing import Optional
import requests
from bs4 import BeautifulSoup

from scrapers.base import BaseScraper, JobRecord
from scrapers.jsonld import strip_html as _strip_html, find_jobpostings, parse_jobposting
from utils.rate_limiter import RateLimiter
from utils.user_agents import get_headers, ACCEPT_ENCODING
from utils.retry import retry
from utils.proxy import apply_proxy, rotate_proxy

logger = logging.getLogger(__name__)

REED_SEARCH_URL = "https://www.reed.co.uk/jobs/{keyword}-jobs"
REED_API_SEARCH = "https://www.reed.co.uk/api/1.0/search"
REED_API_DETAILS = "https://www.reed.co.uk/api/1.0/jobs/{job_id}"


def _reed_headers(referer: str = "https://www.reed.co.uk/") -> dict:
    """Headers that pass Reed's bot detection — must look like a real browser navigation."""
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                  "image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.9",
        "Accept-Encoding": ACCEPT_ENCODING,
        "Referer": referer,
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
    }


def _ddmmyyyy_to_iso(raw: Optional[str]) -> Optional[str]:
    """Reed API dates come as DD/MM/YYYY — convert to ISO date."""
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%d/%m/%Y").date().isoformat()
    except ValueError:
        return raw


class ReedScraper(BaseScraper):
    def __init__(self, config):
        super().__init__(config)
        self.rate_limiter = RateLimiter(config.domain_delays)
        self.session = requests.Session()
        apply_proxy(self.session)
        self._warmed_up = False
        self.api_key = getattr(config, "reed_api_key", "") or ""
        self._api_disabled = False   # set when the key is rejected (401/403)

    def _warmup(self):
        """Visit Reed homepage first to get cookies — prevents 403 on search pages."""
        try:
            self.session.get(
                "https://www.reed.co.uk/",
                headers=_reed_headers(referer="https://www.google.com/"),
                timeout=self.config.request_timeout,
            )
            self._warmed_up = True
            logger.debug("Reed: session warmed up (homepage visited)")
        except Exception as e:
            logger.debug(f"Reed: warmup failed (continuing anyway): {e}")
            self._warmed_up = True   # don't retry

    def scrape(self, keyword: str, location: str) -> list[JobRecord]:
        if self.api_key and not self._api_disabled:
            return self._scrape_api(keyword, location)
        return self._scrape_html(keyword, location)

    # ── Official Reed Jobseeker API (preferred) ──────────────────────────────

    @retry(max_attempts=3, base_delay=2.0)
    def _api_get(self, url: str, params: dict = None):
        self.rate_limiter.wait("www.reed.co.uk")
        return self.session.get(
            url,
            params=params,
            auth=(self.api_key, ""),
            timeout=self.config.request_timeout,
        )

    def _scrape_api(self, keyword: str, location: str) -> list[JobRecord]:
        results = []
        skip = 0
        max_results = self.config.max_results_per_keyword

        while len(results) < max_results:
            params = {
                "keywords": keyword,
                "resultsToTake": min(100, max_results - len(results)),
                "resultsToSkip": skip,
            }
            # Reed's locationName is a place-name geocoder — country names
            # produce ambiguousLocations, so omit it for UK-wide searches.
            if location and location.lower() not in ("united kingdom", "uk"):
                params["locationName"] = location

            try:
                resp = self._api_get(REED_API_SEARCH, params)
            except Exception as e:
                logger.error(f"Reed API request failed for '{keyword}': {e}")
                break

            if resp.status_code in (401, 403):
                logger.error(
                    f"Reed API key rejected (HTTP {resp.status_code}) — check REED_API_KEY. "
                    "Falling back to HTML scraping for the rest of this run."
                )
                self._api_disabled = True
                return self._scrape_html(keyword, location)

            if resp.status_code != 200:
                logger.warning(f"Reed API returned HTTP {resp.status_code} for '{keyword}'")
                break

            try:
                data = resp.json()
            except Exception:
                logger.error("Reed API: response was not JSON")
                break

            batch = data.get("results", []) or []
            if not batch:
                break

            for item in batch:
                if len(results) >= max_results:
                    break
                try:
                    results.append(self._parse_api_job(item))
                except Exception as e:
                    logger.warning(f"Reed API job parse error: {e}")

            total = data.get("totalResults", 0)
            skip += len(batch)
            if skip >= total:
                break

        # Search results only carry a truncated description snippet — fetch
        # full details for the first 20 jobs (mirrors the Indeed pattern).
        for record in results[:20]:
            try:
                self._fetch_api_details(record)
            except Exception as e:
                logger.debug(f"Reed API details fetch failed for {record.job_id}: {e}")

        logger.info(f"Reed API: scraped {len(results)} jobs for '{keyword}'")
        return results

    def _parse_api_job(self, item: dict) -> JobRecord:
        job_id = str(item.get("jobId", ""))
        sal_min = item.get("minimumSalary")
        sal_max = item.get("maximumSalary")
        sal_min = float(sal_min) if sal_min is not None else None
        sal_max = float(sal_max) if sal_max is not None else None

        salary_text = None
        salary_period = None
        if sal_min is not None or sal_max is not None:
            # Heuristic refined later by details salaryType when fetched
            ref = sal_max if sal_max is not None else sal_min
            salary_period = "hourly" if ref is not None and ref < 1000 else "annual"
            period_label = " an hour" if salary_period == "hourly" else " a year"
            if sal_min is not None and sal_max is not None and sal_min != sal_max:
                salary_text = f"£{sal_min:,.2f} - £{sal_max:,.2f}{period_label}".replace(".00", "")
            else:
                salary_text = f"£{(sal_min if sal_min is not None else sal_max):,.2f}{period_label}".replace(".00", "")

        snippet = _strip_html(item.get("jobDescription", "") or "")

        return JobRecord(
            job_id=job_id,
            source="reed",
            title=item.get("jobTitle", "Unknown"),
            company=item.get("employerName"),
            location=item.get("locationName"),
            salary_text=salary_text,
            salary_min=sal_min,
            salary_max=sal_max,
            salary_period=salary_period,
            description=snippet[:2000] if snippet else None,
            posted_at=_ddmmyyyy_to_iso(item.get("date")),
            expires_at=_ddmmyyyy_to_iso(item.get("expirationDate")),
            apply_url=item.get("jobUrl"),
            scraped_at=datetime.utcnow().isoformat() + "Z",
        )

    def _fetch_api_details(self, record: JobRecord):
        resp = self._api_get(REED_API_DETAILS.format(job_id=record.job_id))
        if resp.status_code != 200:
            return
        detail = resp.json()

        full_desc = _strip_html(detail.get("jobDescription", "") or "")
        if full_desc:
            record.description = full_desc[:2000]

        if detail.get("contractType") or detail.get("fullTime") is not None:
            parts = []
            if detail.get("fullTime"):
                parts.append("full-time")
            if detail.get("partTime"):
                parts.append("part-time")
            contract = (detail.get("contractType") or "").lower()
            if contract and contract not in parts:
                parts.append(contract)
            if parts:
                record.job_type = ", ".join(parts)

        salary_type = (detail.get("salaryType") or "").lower()
        if "hour" in salary_type:
            record.salary_period = "hourly"
        elif "annum" in salary_type or "year" in salary_type:
            record.salary_period = "annual"

        if detail.get("externalUrl") and not record.company_url:
            record.company_url = detail["externalUrl"]

    # ── HTML scraping fallback (no API key) ──────────────────────────────────

    def _scrape_html(self, keyword: str, location: str) -> list[JobRecord]:
        if not self._warmed_up:
            self._warmup()

        results = []
        page = 1
        collected = 0
        keyword_slug = keyword.lower().replace(" ", "-")

        while collected < self.config.max_results_per_keyword:
            self.rate_limiter.wait("www.reed.co.uk")
            params = {"location": location, "pageno": page}
            url = REED_SEARCH_URL.format(keyword=keyword_slug)

            for attempt in range(self.config.max_retries):
                try:
                    resp = self.session.get(
                        url,
                        params=params,
                        headers=_reed_headers(),
                        timeout=self.config.request_timeout,
                    )
                    break
                except Exception as e:
                    logger.error(f"Reed request failed for '{keyword}' page {page}: {e}")
                    resp = None
            else:
                break  # all retries exhausted

            if resp is None:
                break

            if resp.status_code == 403:
                if page == 1:
                    # First page 403: re-warm and retry once
                    logger.warning("Reed: 403 on first page — refreshing session and retrying")
                    rotate_proxy(self.session)
                    self._warmed_up = False
                    self._warmup()
                    time.sleep(3)
                    try:
                        resp = self.session.get(
                            url, params=params,
                            headers=_reed_headers(),
                            timeout=self.config.request_timeout,
                        )
                    except Exception:
                        break
                    if resp.status_code != 200:
                        logger.warning(f"Reed: still {resp.status_code} after session refresh — skipping")
                        break
                else:
                    logger.warning(f"Reed: 403 on page {page}, stopping pagination")
                    break

            if resp.status_code == 429 or resp.status_code == 503:
                retry_after = int(resp.headers.get("Retry-After", 15))
                logger.warning(f"Reed rate limited ({resp.status_code}), waiting {retry_after}s")
                time.sleep(retry_after)
                continue

            if resp.status_code != 200:
                logger.warning(f"Reed returned HTTP {resp.status_code} for '{keyword}'")
                break

            soup = BeautifulSoup(resp.text, "lxml")
            page_jobs = find_jobpostings(soup)

            if not page_jobs:
                # Fallback: try to parse job cards from HTML
                page_jobs_fallback = self._parse_job_cards(soup)
                if not page_jobs_fallback:
                    logger.debug(f"Reed: no more jobs found on page {page} for '{keyword}'")
                    break
                for job_data in page_jobs_fallback:
                    if collected >= self.config.max_results_per_keyword:
                        break
                    results.append(job_data)
                    collected += 1
            else:
                for job_data in page_jobs:
                    if collected >= self.config.max_results_per_keyword:
                        break
                    try:
                        record = parse_jobposting(job_data, "reed")
                        results.append(record)
                        collected += 1
                    except Exception as e:
                        logger.warning(f"Reed JSON-LD parse error: {e}")

            # Check if there's a next page
            next_btn = soup.find("a", {"data-page": str(page + 1)}) or soup.find("a", string=re.compile(r"Next", re.I))
            if not next_btn and len(page_jobs) == 0:
                break
            page += 1

        logger.info(f"Reed: scraped {len(results)} jobs for '{keyword}'")
        return results

    def _parse_job_cards(self, soup: BeautifulSoup) -> list[JobRecord]:
        """Fallback: parse job cards from HTML when JSON-LD is missing."""
        records = []
        cards = soup.find_all("article", attrs={"data-job-id": True})
        for card in cards:
            try:
                job_id = card.get("data-job-id", "")
                title_el = card.find("h2") or card.find(class_=re.compile(r"title", re.I))
                title = title_el.get_text(strip=True) if title_el else "Unknown"
                company_el = card.find(class_=re.compile(r"employer|company", re.I))
                company = company_el.get_text(strip=True) if company_el else None
                location_el = card.find(class_=re.compile(r"location", re.I))
                location = location_el.get_text(strip=True) if location_el else None
                apply_url = f"https://www.reed.co.uk/jobs/{job_id}" if job_id else None

                records.append(JobRecord(
                    job_id=str(job_id),
                    source="reed",
                    title=title,
                    company=company,
                    location=location,
                    apply_url=apply_url,
                    scraped_at=datetime.utcnow().isoformat() + "Z",
                ))
            except Exception as e:
                logger.debug(f"Reed card parse error: {e}")
        return records
