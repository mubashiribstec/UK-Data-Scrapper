import time
import logging
from difflib import SequenceMatcher
from typing import Optional
from datetime import datetime
import requests
from bs4 import BeautifulSoup

from processing.cleaner import clean_phone, clean_email
from processing.merger import ContactRecord

logger = logging.getLogger(__name__)

CC_API = "https://api.charitycommission.gov.uk/register/api"
CC_SEARCH_URL = "https://register-of-charities.charitycommission.gov.uk/charity-search"
OSCR_URL = "https://www.oscr.org.uk/umbraco/Surface/CharityRegister/GetCharityDetails"


def enrich_from_charities(company: str, timeout: int = 10) -> Optional[ContactRecord]:
    record = ContactRecord(company=company)
    record.enrichment_sources = ["charities"]

    session = requests.Session()
    session.headers.update({
        "User-Agent": "NurseJobsScraper/1.0",
        "Accept": "application/json",
    })

    # Try Charity Commission API
    try:
        time.sleep(0.5)
        resp = session.get(
            f"{CC_API}/charitySearch",
            params={"q": company},
            timeout=timeout,
        )
        if resp.status_code == 200:
            data = resp.json()
            charities = data if isinstance(data, list) else data.get("charities", data.get("data", []))

            best = None
            best_score = 0.0
            for c in charities[:5]:
                name = c.get("charity_name", c.get("name", ""))
                score = SequenceMatcher(None, company.lower(), name.lower()).ratio()
                if score > best_score:
                    best_score = score
                    best = c

            if best and best_score >= 0.70:
                raw_phone = best.get("charity_contact_phone", best.get("phone", ""))
                raw_email = best.get("charity_contact_email", best.get("email", ""))
                address_parts = [
                    best.get("charity_contact_address1", ""),
                    best.get("charity_contact_address2", ""),
                    best.get("charity_contact_address3", ""),
                    best.get("charity_contact_postcode", ""),
                ]
                address = ", ".join(p for p in address_parts if p) or None
                website = best.get("charity_contact_web", best.get("website", ""))

                phone = clean_phone(raw_phone) if raw_phone else None
                email = clean_email(raw_email) if raw_email else None

                if phone:
                    record.phone_numbers.append(phone)
                if email:
                    record.emails.append(email)
                if address:
                    record.address = address
                if website:
                    record.company_website = website
                record.company_type = "charity"
                record.enriched_at = datetime.utcnow().isoformat() + "Z"

                if record.phone_numbers or record.emails or record.address:
                    return record
    except Exception as e:
        logger.debug(f"Charity Commission API failed for '{company}': {e}")

    # HTML scraping fallback
    try:
        time.sleep(1)
        resp = session.get(
            CC_SEARCH_URL,
            params={"q": company},
            headers={"Accept": "text/html"},
            timeout=timeout,
        )
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "lxml")
            from processing.cleaner import extract_phones, extract_emails
            text = soup.get_text(separator=" ")
            phones = extract_phones(text)
            emails = extract_emails(text)
            if phones or emails:
                record.phone_numbers = phones[:3]
                record.emails = emails[:3]
                record.company_type = "charity"
                record.enriched_at = datetime.utcnow().isoformat() + "Z"
                return record
    except Exception as e:
        logger.debug(f"Charity Commission HTML scrape failed for '{company}': {e}")

    return None
