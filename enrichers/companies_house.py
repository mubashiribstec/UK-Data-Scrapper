import time
import logging
from difflib import SequenceMatcher
from typing import Optional
from datetime import datetime
import requests

from processing.merger import ContactRecord

logger = logging.getLogger(__name__)

CH_BASE = "https://api.company-information.service.gov.uk"
DOMAIN = "api.company-information.service.gov.uk"


def _format_address(addr: dict) -> Optional[str]:
    if not addr or not isinstance(addr, dict):
        return None
    parts = [
        addr.get("premises"),
        addr.get("address_line_1"),
        addr.get("address_line_2"),
        addr.get("locality"),
        addr.get("region"),
        addr.get("postal_code"),
        addr.get("country"),
    ]
    return ", ".join(p for p in parts if p) or None


def _fuzzy_match(name1: str, name2: str) -> float:
    return SequenceMatcher(None, name1.lower(), name2.lower()).ratio()


def enrich_from_companies_house(
    company: str,
    timeout: int = 10,
    min_similarity: float = 0.75,
) -> Optional[ContactRecord]:
    record = ContactRecord(company=company)
    record.enrichment_sources = ["companies_house"]

    session = requests.Session()
    session.headers.update({"User-Agent": "NurseJobsScraper/1.0"})

    # Step 1: Search
    try:
        time.sleep(0.5)
        resp = session.get(
            f"{CH_BASE}/search/companies",
            params={"q": company, "items_per_page": 5},
            timeout=timeout,
        )
        resp.raise_for_status()
        results = resp.json().get("items", [])
    except Exception as e:
        logger.warning(f"Companies House search failed for '{company}': {e}")
        return None

    # Find best fuzzy match
    best_match = None
    best_score = 0.0
    for item in results:
        score = _fuzzy_match(company, item.get("title", ""))
        if score > best_score:
            best_score = score
            best_match = item

    if not best_match or best_score < min_similarity:
        logger.debug(f"Companies House: no good match for '{company}' (best={best_score:.2f})")
        return None

    company_number = best_match.get("company_number", "")
    record.company_number = company_number
    record.company_type = best_match.get("company_type")

    # Step 2: Company profile
    try:
        time.sleep(0.5)
        resp = session.get(f"{CH_BASE}/company/{company_number}", timeout=timeout)
        resp.raise_for_status()
        profile = resp.json()
        addr = profile.get("registered_office_address", {})
        record.address = _format_address(addr)
        if profile.get("company_status") == "dissolved":
            logger.debug(f"Companies House: '{company}' is dissolved, skipping")
            return None
    except Exception as e:
        logger.warning(f"Companies House profile failed for {company_number}: {e}")

    # Step 3: Officers
    try:
        time.sleep(0.5)
        resp = session.get(
            f"{CH_BASE}/company/{company_number}/officers",
            params={"items_per_page": 10},
            timeout=timeout,
        )
        resp.raise_for_status()
        officers = resp.json().get("items", [])
        for officer in officers:
            if officer.get("resigned_on"):
                continue
            role = officer.get("officer_role", "").lower()
            name = officer.get("name", "")
            if any(r in role for r in ["secretary", "director", "manager"]):
                record.contact_person = name
                break
    except Exception as e:
        logger.debug(f"Companies House officers failed for {company_number}: {e}")

    record.enriched_at = datetime.utcnow().isoformat() + "Z"

    if record.address or record.company_number:
        return record
    return None
