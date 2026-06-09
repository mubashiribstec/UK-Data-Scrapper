import re
import json
import time
import logging
from datetime import datetime
from typing import Optional
import requests
from bs4 import BeautifulSoup

from scrapers.base import BaseScraper, JobRecord
from utils.rate_limiter import RateLimiter
from utils.user_agents import get_headers

logger = logging.getLogger(__name__)

REED_SEARCH_URL = "https://www.reed.co.uk/jobs/{keyword}-jobs"


def _strip_html(html_text: str) -> str:
    if not html_text:
        return ""
    soup = BeautifulSoup(html_text, "lxml")
    return soup.get_text(separator=" ", strip=True)


def _parse_salary_ld(salary_obj: dict) -> tuple[Optional[float], Optional[float], Optional[str]]:
    if not salary_obj or not isinstance(salary_obj, dict):
        return None, None, None
    value = salary_obj.get("value", {})
    if isinstance(value, dict):
        min_val = value.get("minValue")
        max_val = value.get("maxValue")
        unit = value.get("unitText", "").lower()
    else:
        min_val = max_val = value
        unit = salary_obj.get("unitText", "").lower()

    period = None
    if "hour" in unit:
        period = "hourly"
    elif "year" in unit or "annual" in unit or "month" in unit:
        period = "annual"

    try:
        sal_min = float(min_val) if min_val else None
        sal_max = float(max_val) if max_val else None
    except (TypeError, ValueError):
        sal_min = sal_max = None

    return sal_min, sal_max, period


class ReedScraper(BaseScraper):
    def __init__(self, config):
        super().__init__(config)
        self.rate_limiter = RateLimiter(config.domain_delays)
        self.session = requests.Session()

    def scrape(self, keyword: str, location: str) -> list[JobRecord]:
        results = []
        page = 1
        collected = 0
        keyword_slug = keyword.lower().replace(" ", "-")

        while collected < self.config.max_results_per_keyword:
            self.rate_limiter.wait("www.reed.co.uk")
            params = {"location": location, "pageno": page}
            url = REED_SEARCH_URL.format(keyword=keyword_slug)

            try:
                resp = self.session.get(
                    url,
                    params=params,
                    headers=get_headers(),
                    timeout=self.config.request_timeout,
                )
            except Exception as e:
                logger.error(f"Reed request failed for '{keyword}' page {page}: {e}")
                break

            if resp.status_code == 429 or resp.status_code == 503:
                retry_after = int(resp.headers.get("Retry-After", 10))
                logger.warning(f"Reed rate limited (HTTP {resp.status_code}), waiting {retry_after}s")
                time.sleep(retry_after)
                continue

            if resp.status_code != 200:
                logger.warning(f"Reed returned HTTP {resp.status_code} for '{keyword}'")
                break

            soup = BeautifulSoup(resp.text, "lxml")
            ld_json_tags = soup.find_all("script", type="application/ld+json")

            page_jobs = []
            for tag in ld_json_tags:
                try:
                    data = json.loads(tag.string or "")
                    if isinstance(data, list):
                        for item in data:
                            if item.get("@type") == "JobPosting":
                                page_jobs.append(item)
                    elif isinstance(data, dict):
                        if data.get("@type") == "JobPosting":
                            page_jobs.append(data)
                        elif data.get("@type") == "ItemList":
                            for item in data.get("itemListElement", []):
                                if isinstance(item, dict) and item.get("@type") == "JobPosting":
                                    page_jobs.append(item)
                except (json.JSONDecodeError, AttributeError):
                    pass

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
                        record = self._parse_ld_json(job_data)
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

    def _parse_ld_json(self, data: dict) -> JobRecord:
        identifier = data.get("identifier", {})
        job_id = str(identifier.get("value", "")) if isinstance(identifier, dict) else str(identifier)
        if not job_id:
            job_id = data.get("url", "").split("/")[-1] or str(hash(data.get("title", "")))

        title = data.get("title", "Unknown")
        org = data.get("hiringOrganization", {}) or {}
        company = org.get("name") if isinstance(org, dict) else None
        company_url = org.get("sameAs") if isinstance(org, dict) else None

        job_location = data.get("jobLocation", {})
        address = {}
        if isinstance(job_location, list) and job_location:
            job_location = job_location[0]
        if isinstance(job_location, dict):
            address = job_location.get("address", {}) or {}

        location_city = address.get("addressLocality") if isinstance(address, dict) else None
        location_postcode = address.get("postalCode") if isinstance(address, dict) else None
        location_region = address.get("addressRegion") if isinstance(address, dict) else None
        location_parts = [p for p in [location_city, location_region, location_postcode] if p]
        location = ", ".join(location_parts) if location_parts else None

        salary_obj = data.get("baseSalary", {})
        sal_min, sal_max, sal_period = _parse_salary_ld(salary_obj)
        salary_text = data.get("salaryCurrency", "")

        posted_at = data.get("datePosted")
        expires_at = data.get("validThrough")
        job_type_raw = data.get("employmentType", "")
        if isinstance(job_type_raw, list):
            job_type = ", ".join(job_type_raw).lower()
        else:
            job_type = str(job_type_raw).lower() if job_type_raw else None

        apply_url = data.get("url")
        description = _strip_html(data.get("description", ""))

        return JobRecord(
            job_id=job_id,
            source="reed",
            title=title,
            company=company,
            company_url=company_url,
            location=location,
            location_city=location_city,
            location_postcode=location_postcode,
            salary_text=salary_text or None,
            salary_min=sal_min,
            salary_max=sal_max,
            salary_period=sal_period,
            job_type=job_type,
            description=description[:2000] if description else None,
            posted_at=posted_at,
            expires_at=expires_at,
            apply_url=apply_url,
            scraped_at=datetime.utcnow().isoformat() + "Z",
        )

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
