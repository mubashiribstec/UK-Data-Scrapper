"""Shared schema.org JobPosting (JSON-LD) extraction and HTML helpers.

`strip_html` is used by Reed (API description cleanup) and Indeed (mosaic
snippet cleanup). The JobPosting parsers remain for any source that embeds
schema.org JSON-LD job data for SEO, which is far more stable than CSS selectors.
"""

import json
import logging
from datetime import datetime
from typing import Optional

from bs4 import BeautifulSoup

from scrapers.base import JobRecord

logger = logging.getLogger(__name__)


def strip_html(html_text: str) -> str:
    if not html_text:
        return ""
    soup = BeautifulSoup(html_text, "lxml")
    return soup.get_text(separator=" ", strip=True)


def parse_salary_ld(salary_obj: dict) -> tuple[Optional[float], Optional[float], Optional[str]]:
    if not salary_obj or not isinstance(salary_obj, dict):
        return None, None, None
    value = salary_obj.get("value", {})
    if isinstance(value, dict):
        min_val = value.get("minValue")
        max_val = value.get("maxValue")
        unit = str(value.get("unitText", "")).lower()
    else:
        min_val = max_val = value
        unit = str(salary_obj.get("unitText", "")).lower()

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


def find_jobpostings(soup: BeautifulSoup) -> list[dict]:
    """Collect every schema.org JobPosting dict from a page's JSON-LD scripts."""
    postings = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, AttributeError):
            continue
        candidates = data if isinstance(data, list) else [data]
        for item in candidates:
            if not isinstance(item, dict):
                continue
            if item.get("@type") == "JobPosting":
                postings.append(item)
            elif item.get("@type") == "ItemList":
                for el in item.get("itemListElement", []):
                    if isinstance(el, dict):
                        inner = el.get("item", el)
                        if isinstance(inner, dict) and inner.get("@type") == "JobPosting":
                            postings.append(inner)
            elif "@graph" in item:
                for el in item["@graph"]:
                    if isinstance(el, dict) and el.get("@type") == "JobPosting":
                        postings.append(el)
    return postings


def parse_jobposting(data: dict, source: str) -> JobRecord:
    """Convert a schema.org JobPosting dict into a JobRecord."""
    identifier = data.get("identifier", {})
    job_id = str(identifier.get("value", "")) if isinstance(identifier, dict) else str(identifier)
    if not job_id or job_id == "None":
        job_id = data.get("url", "").rstrip("/").split("/")[-1] or str(abs(hash(data.get("title", ""))))

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

    sal_min, sal_max, sal_period = parse_salary_ld(data.get("baseSalary", {}))

    currency = data.get("salaryCurrency", "GBP")
    symbol = "£" if currency in ("GBP", "") else currency + " "
    if sal_min is not None and sal_max is not None:
        period_label = {"hourly": " an hour", "annual": " a year"}.get(sal_period, "")
        if sal_min == sal_max:
            salary_text = f"{symbol}{sal_min:,.2f}{period_label}".replace(".00", "")
        else:
            salary_text = f"{symbol}{sal_min:,.2f} - {symbol}{sal_max:,.2f}{period_label}".replace(".00", "")
    else:
        salary_text = None

    job_type_raw = data.get("employmentType", "")
    if isinstance(job_type_raw, list):
        job_type = ", ".join(job_type_raw).lower()
    else:
        job_type = str(job_type_raw).lower() if job_type_raw else None

    description = strip_html(data.get("description", ""))

    requirements = []
    for field_key in ("qualifications", "experienceRequirements", "skills"):
        val = data.get(field_key)
        if isinstance(val, list):
            requirements.extend(str(v) for v in val if v)
        elif isinstance(val, str) and val:
            requirements.extend(line.strip() for line in val.splitlines() if line.strip())

    benefits = []
    bens_raw = data.get("jobBenefits", "")
    if isinstance(bens_raw, list):
        benefits = [str(b) for b in bens_raw if b]
    elif isinstance(bens_raw, str) and bens_raw:
        benefits = [line.strip() for line in bens_raw.splitlines() if line.strip()]

    return JobRecord(
        job_id=job_id,
        source=source,
        title=title,
        company=company,
        company_url=company_url,
        location=location,
        location_city=location_city,
        location_postcode=location_postcode,
        salary_text=salary_text,
        salary_min=sal_min,
        salary_max=sal_max,
        salary_period=sal_period,
        job_type=job_type,
        description=description[:2000] if description else None,
        requirements=requirements,
        benefits=benefits,
        posted_at=data.get("datePosted"),
        expires_at=data.get("validThrough"),
        apply_url=data.get("url"),
        scraped_at=datetime.utcnow().isoformat() + "Z",
    )
