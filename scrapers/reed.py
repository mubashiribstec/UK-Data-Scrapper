import logging
from datetime import datetime
from typing import Optional
import requests

from scrapers.base import BaseScraper, JobRecord
from scrapers.jsonld import strip_html as _strip_html
from utils.rate_limiter import RateLimiter
from utils.retry import retry
from utils.proxy import apply_proxy

logger = logging.getLogger(__name__)

REED_API_SEARCH = "https://www.reed.co.uk/api/1.0/search"
REED_API_DETAILS = "https://www.reed.co.uk/api/1.0/jobs/{job_id}"


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
        self.api_key = getattr(config, "reed_api_key", "") or ""

    def scrape(self, keyword: str, location: str) -> list[JobRecord]:
        if not self.api_key:
            logger.warning(
                "Reed: no REED_API_KEY configured — skipping Reed entirely "
                "(register a free key at reed.co.uk/developers)"
            )
            return []
        return self._scrape_api(keyword, location)

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
                    "Skipping Reed for the rest of this run."
                )
                return results

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
