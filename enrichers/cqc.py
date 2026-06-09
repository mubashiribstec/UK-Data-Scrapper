import time
import logging
from difflib import SequenceMatcher
from typing import Optional
from datetime import datetime
import requests

from processing.cleaner import clean_phone
from processing.merger import ContactRecord

logger = logging.getLogger(__name__)

CQC_API = "https://api.cqc.org.uk/public/v1"


def enrich_from_cqc(company: str, timeout: int = 10) -> Optional[ContactRecord]:
    record = ContactRecord(company=company)
    record.enrichment_sources = ["cqc"]

    session = requests.Session()
    session.headers.update({"User-Agent": "NurseJobsScraper/1.0"})

    try:
        time.sleep(1.0)
        resp = session.get(
            f"{CQC_API}/providers",
            params={"name": company, "perPage": 5},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        providers = data.get("providers", [])
    except Exception as e:
        logger.debug(f"CQC API failed for '{company}': {e}")
        return None

    # Find best match
    best = None
    best_score = 0.0
    for p in providers:
        name = p.get("name", "")
        score = SequenceMatcher(None, company.lower(), name.lower()).ratio()
        if score > best_score:
            best_score = score
            best = p

    if not best or best_score < 0.70:
        return None

    provider_id = best.get("providerId", "")
    raw_phone = best.get("phone", best.get("telephone", ""))
    address_line1 = best.get("postalAddressLine1", "")
    address_line2 = best.get("postalAddressLine2", "")
    locality = best.get("postalAddressTownCity", "")
    postcode = best.get("postalCode", "")
    website = best.get("website", "")

    address_parts = [address_line1, address_line2, locality, postcode]
    address = ", ".join(p for p in address_parts if p) or None

    phone = clean_phone(raw_phone) if raw_phone else None
    if phone:
        record.phone_numbers.append(phone)
    if address:
        record.address = address
    if website:
        record.company_website = website
    record.company_type = best.get("type", "care_provider")

    # Fetch full provider detail if needed
    if provider_id and not (record.phone_numbers and record.address):
        try:
            time.sleep(1.0)
            resp = session.get(f"{CQC_API}/providers/{provider_id}", timeout=timeout)
            if resp.status_code == 200:
                detail = resp.json()
                if not phone:
                    raw_p = detail.get("phone", detail.get("telephone", ""))
                    p = clean_phone(raw_p) if raw_p else None
                    if p:
                        record.phone_numbers.append(p)
                contacts = detail.get("contacts", [])
                for contact in contacts[:1]:
                    name = contact.get("personTitle", "") + " " + contact.get("personGivenName", "") + " " + contact.get("personFamilyName", "")
                    name = name.strip()
                    if name:
                        record.contact_person = name
        except Exception as e:
            logger.debug(f"CQC detail failed for provider {provider_id}: {e}")

    record.enriched_at = datetime.utcnow().isoformat() + "Z"

    if record.phone_numbers or record.address:
        return record
    return None
