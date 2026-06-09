import re
import json
import logging
from typing import Optional
from datetime import datetime
import requests
from bs4 import BeautifulSoup

from processing.cleaner import extract_phones, extract_emails, UK_POSTCODE_RE
from processing.merger import ContactRecord

logger = logging.getLogger(__name__)

CONTACT_PATHS = ["/contact", "/contact-us", "/about", "/about-us", "/team", "/our-team", "/staff"]
CONTACT_PERSON_RE = re.compile(
    r"(?:contact|speak to|talk to|email)\s+([A-Z][a-z]+ [A-Z][a-z]+)|"
    r"([A-Z][a-z]+ [A-Z][a-z]+)[,\s]+(?:HR|Recruitment|People|Hiring|Manager|Director)",
    re.I
)


def _guess_urls(company_name: str) -> list[str]:
    slug = re.sub(r"[^\w]", "", company_name.lower())
    return [
        f"https://{slug}.nhs.uk",
        f"https://www.{slug}.nhs.uk",
        f"https://{slug}.org.uk",
        f"https://www.{slug}.org.uk",
        f"https://{slug}.co.uk",
        f"https://www.{slug}.co.uk",
        f"https://{slug}.com",
    ]


def _verify_url(url: str, timeout: int = 5) -> bool:
    try:
        resp = requests.head(url, timeout=timeout, allow_redirects=True)
        return resp.status_code < 400
    except Exception:
        return False


def _extract_address_from_ld(soup: BeautifulSoup) -> Optional[str]:
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
            items = data if isinstance(data, list) else [data]
            for item in items:
                addr = item.get("address") or (item.get("location") or {}).get("address")
                if addr and isinstance(addr, dict):
                    parts = [
                        addr.get("streetAddress"),
                        addr.get("addressLocality"),
                        addr.get("addressRegion"),
                        addr.get("postalCode"),
                        addr.get("addressCountry"),
                    ]
                    return ", ".join(p for p in parts if p)
        except Exception:
            pass
    return None


def _extract_address_from_text(text: str) -> Optional[str]:
    """Find UK postcode context in text."""
    postcode_match = UK_POSTCODE_RE.search(text)
    if not postcode_match:
        return None
    # Grab surrounding context (100 chars before)
    start = max(0, postcode_match.start() - 100)
    context = text[start:postcode_match.end() + 20]
    # Clean up
    context = re.sub(r"\s+", " ", context).strip()
    return context[:200]


def enrich_from_website(
    company: str,
    company_url: Optional[str] = None,
    timeout: int = 10,
) -> Optional[ContactRecord]:
    record = ContactRecord(company=company)
    record.enrichment_sources = ["website"]

    # Determine URL to use
    base_url = company_url
    if not base_url:
        for candidate in _guess_urls(company):
            if _verify_url(candidate, timeout=5):
                base_url = candidate
                logger.debug(f"Website enricher: guessed URL {base_url} for '{company}'")
                break

    if not base_url:
        return None

    record.company_website = base_url
    all_phones = []
    all_emails = []
    all_names = []
    address_found = None

    pages_to_fetch = [base_url] + [base_url.rstrip("/") + path for path in CONTACT_PATHS]

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (compatible; NurseJobsScraper/1.0)",
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    })

    for url in pages_to_fetch:
        try:
            resp = session.get(url, timeout=timeout, allow_redirects=True)
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, "lxml")
            text = soup.get_text(separator=" ", strip=True)

            phones = extract_phones(text)
            emails = extract_emails(text)
            all_phones.extend(phones)
            all_emails.extend(emails)

            # Contact person extraction
            for match in CONTACT_PERSON_RE.finditer(text):
                name = match.group(1) or match.group(2)
                if name:
                    all_names.append(name.strip())

            # Address from JSON-LD
            if not address_found:
                address_found = _extract_address_from_ld(soup)
            if not address_found:
                address_found = _extract_address_from_text(text)

            if phones and emails:
                # Enough data found
                break

        except Exception as e:
            logger.debug(f"Website enricher: failed to fetch {url}: {e}")

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

    if all_names:
        record.contact_person = all_names[0]

    if address_found:
        record.address = address_found

    record.enriched_at = datetime.utcnow().isoformat() + "Z"

    if record.phone_numbers or record.emails or record.address:
        return record
    return None
