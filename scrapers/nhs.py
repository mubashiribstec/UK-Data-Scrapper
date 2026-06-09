import re
import time
import logging
from datetime import datetime
from typing import Optional
import requests

from scrapers.base import BaseScraper, JobRecord
from utils.rate_limiter import RateLimiter
from utils.user_agents import get_headers

logger = logging.getLogger(__name__)

NHS_SEARCH_URL = "https://api.jobs.nhs.uk/api/v1/vacancy/search"
NHS_DETAIL_URL = "https://api.jobs.nhs.uk/api/v1/vacancy/{id}"
NHS_APPLY_BASE = "https://www.jobs.nhs.uk/candidate/jobadvert/{id}"


def parse_salary(text: str) -> tuple[Optional[float], Optional[float], Optional[str]]:
    """Parse NHS-style salary strings like '£28,407 - £34,581 a year' or '£14.56 an hour'."""
    if not text:
        return None, None, None

    text_lower = text.lower()
    period = None
    if "hour" in text_lower:
        period = "hourly"
    elif "year" in text_lower or "annum" in text_lower or "per year" in text_lower:
        period = "annual"

    amounts = re.findall(r"£([\d,]+(?:\.\d{1,2})?)", text)
    parsed = []
    for a in amounts:
        try:
            parsed.append(float(a.replace(",", "")))
        except ValueError:
            pass

    if len(parsed) == 0:
        return None, None, period
    elif len(parsed) == 1:
        # "Up to £X" or "£X"
        if "up to" in text_lower:
            return None, parsed[0], period
        return parsed[0], parsed[0], period
    else:
        return min(parsed), max(parsed), period


class NHSScraper(BaseScraper):
    def __init__(self, config):
        super().__init__(config)
        self.rate_limiter = RateLimiter(config.domain_delays)
        self.session = requests.Session()
        # NHS API requires JSON accept header — without it the API returns HTML
        self.session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
        })

    def scrape(self, keyword: str, location: str) -> list[JobRecord]:
        results = []
        page = 1
        page_size = 20
        collected = 0

        while collected < self.config.max_results_per_keyword:
            self.rate_limiter.wait("api.jobs.nhs.uk")
            params = {
                "keyword": keyword,
                "language": "en",
                "page": page,
                "pageSize": page_size,
                "sortBy": "PublishedDesc",
            }
            try:
                resp = self.session.get(
                    NHS_SEARCH_URL,
                    params=params,
                    headers=get_headers(),
                    timeout=self.config.request_timeout,
                )
                resp.raise_for_status()

                if not resp.content:
                    logger.warning(f"NHS: empty response for '{keyword}' page {page} (HTTP {resp.status_code})")
                    break

                content_type = resp.headers.get("Content-Type", "")
                if "json" not in content_type:
                    preview = resp.text[:200].replace("\n", " ")
                    logger.warning(
                        f"NHS: expected JSON but got '{content_type}' for '{keyword}'. "
                        f"Preview: {preview!r}"
                    )
                    break

                data = resp.json()
            except requests.exceptions.JSONDecodeError:
                preview = resp.text[:200].replace("\n", " ") if resp.text else "(empty)"
                logger.error(
                    f"NHS: JSON decode failed for '{keyword}' page {page}. "
                    f"Status={resp.status_code} body={preview!r}"
                )
                break
            except Exception as e:
                logger.error(f"NHS search page {page} failed for '{keyword}': {e}")
                break

            vacancies = data.get("vacancies", []) or data.get("data", [])
            if not vacancies:
                # Try alternate response shapes
                if isinstance(data, list):
                    vacancies = data
                else:
                    logger.debug(f"NHS response keys: {list(data.keys())}")
                    break

            for v in vacancies:
                if collected >= self.config.max_results_per_keyword:
                    break
                try:
                    record = self._parse_vacancy(v)
                    results.append(record)
                    collected += 1
                except Exception as e:
                    logger.warning(f"Failed to parse NHS vacancy: {e}")

            has_next = data.get("hasNextPage", False)
            total = data.get("totalResults", data.get("total", 0))
            logger.debug(f"NHS page {page}: got {len(vacancies)} vacancies, hasNextPage={has_next}, total={total}")

            if not has_next or len(vacancies) < page_size:
                break
            page += 1

        logger.info(f"NHS Jobs: scraped {len(results)} jobs for '{keyword}'")
        return results

    def _parse_vacancy(self, v: dict) -> JobRecord:
        job_id = str(v.get("id", v.get("jobId", "")))
        title = v.get("jobTitle", v.get("title", "Unknown"))
        company = v.get("employerName", v.get("employer", None))
        location = v.get("location", v.get("jobLocation", None))
        salary_text = v.get("salaryRange", v.get("salary", None))
        posted_at = v.get("publicationDate", v.get("postedDate", None))
        expires_at = v.get("closingDate", v.get("expiryDate", None))
        job_type = v.get("contractType", v.get("jobType", None))

        salary_min, salary_max, salary_period = parse_salary(salary_text)

        apply_url = NHS_APPLY_BASE.format(id=job_id)
        employer_url = v.get("employerUrl", None)

        # Try to get postcode from location
        location_postcode = None
        location_city = None
        if location:
            postcode_match = re.search(r"[A-Z]{1,2}[0-9][0-9A-Z]?\s?[0-9][A-Z]{2}", str(location).upper())
            if postcode_match:
                location_postcode = postcode_match.group()
            parts = str(location).split(",")
            if parts:
                location_city = parts[0].strip()

        scraped_at = datetime.utcnow().isoformat() + "Z"

        record = JobRecord(
            job_id=job_id,
            source="nhs_jobs",
            title=title,
            company=company,
            company_url=employer_url,
            location=location,
            location_city=location_city,
            location_postcode=location_postcode,
            salary_text=salary_text,
            salary_min=salary_min,
            salary_max=salary_max,
            salary_period=salary_period,
            job_type=job_type,
            posted_at=posted_at,
            expires_at=expires_at,
            apply_url=apply_url,
            scraped_at=scraped_at,
        )

        # Fetch full description and requirements (best effort)
        try:
            detail = self._fetch_detail(job_id)
            if detail:
                record.description = detail.get("jobDescription", detail.get("description", None))
                employer_url_detail = detail.get("employerUrl", None)
                if employer_url_detail and not record.company_url:
                    record.company_url = employer_url_detail
                # Extract requirements list
                reqs = detail.get("requirements", detail.get("essentialCriteria", []))
                if isinstance(reqs, list):
                    record.requirements = [str(r) for r in reqs if r]
                elif isinstance(reqs, str) and reqs:
                    record.requirements = [line.strip() for line in reqs.splitlines() if line.strip()]
                # Extract benefits list
                bens = detail.get("benefits", detail.get("employeeBenefits", []))
                if isinstance(bens, list):
                    record.benefits = [str(b) for b in bens if b]
                elif isinstance(bens, str) and bens:
                    record.benefits = [line.strip() for line in bens.splitlines() if line.strip()]
        except Exception as e:
            logger.debug(f"Could not fetch NHS detail for {job_id}: {e}")

        return record

    def _fetch_detail(self, job_id: str) -> Optional[dict]:
        self.rate_limiter.wait("api.jobs.nhs.uk")
        url = NHS_DETAIL_URL.format(id=job_id)
        resp = self.session.get(url, headers=get_headers(), timeout=self.config.request_timeout)
        if resp.status_code == 200 and resp.content:
            try:
                return resp.json()
            except Exception:
                return None
        return None
