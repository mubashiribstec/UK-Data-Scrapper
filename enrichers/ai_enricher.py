import logging
from typing import Optional
from datetime import datetime

from processing.cleaner import clean_phone, clean_email
from processing.merger import ContactRecord
from utils.ai_client import ask_ai, parse_ai_json, get_call_count, reset_counter  # noqa: F401 (re-exported)

logger = logging.getLogger(__name__)

AI_PROMPT_TEMPLATE = """You are a UK business contact researcher. Given a company name, find its
phone number, email address, and main contact person for recruitment/HR.

Company name: {company_name}
Industry: {industry_hint}
Known location: {location}

Search your knowledge for this specific UK organisation's contact details.
If you find details, respond ONLY with a JSON object in this exact format:
{{
  "phone": "+44 XXX XXX XXXX or null",
  "email": "contact@example.com or null",
  "contact_person": "Name and role or null",
  "address": "Full UK address or null",
  "website": "https://... or null",
  "confidence": "high|medium|low",
  "notes": "brief explanation of source"
}}
If you are not confident (confidence = low), still output the JSON but set fields to null.
Do not output anything other than the JSON object."""


def enrich_with_ai(
    company: str,
    location: Optional[str] = None,
    company_type: Optional[str] = None,
    config=None,
) -> Optional[ContactRecord]:
    call_limit = getattr(config, "ai_call_limit", 20) if config else 20
    if get_call_count() >= call_limit:
        logger.warning(f"AI enricher: call limit ({call_limit}) reached, skipping further AI calls")
        return None

    industry_hint = company_type or "healthcare/nursing provider"
    prompt = AI_PROMPT_TEMPLATE.format(
        company_name=company,
        industry_hint=industry_hint,
        location=location or "United Kingdom",
    )

    logger.info(f"AI enricher: looking up '{company}' (call #{get_call_count() + 1})")
    raw_response = ask_ai(prompt, config, timeout=60)
    if not raw_response:
        return None

    data = parse_ai_json(raw_response)
    if not data:
        logger.warning(f"AI enricher: could not parse response for '{company}'")
        return None

    confidence = data.get("confidence", "low")
    logger.info(f"AI enricher: confidence={confidence} for '{company}'")

    record = ContactRecord(company=company)
    record.enrichment_sources = ["ai"]
    record.ai_used = True

    raw_phone = data.get("phone")
    raw_email = data.get("email")
    raw_person = data.get("contact_person")
    raw_address = data.get("address")
    raw_website = data.get("website")

    if raw_phone and raw_phone != "null":
        phone = clean_phone(raw_phone)
        if phone:
            record.phone_numbers.append(phone)
        elif confidence != "low":
            record.phone_numbers.append(raw_phone)

    if raw_email and raw_email != "null":
        email = clean_email(raw_email)
        if email:
            record.emails.append(email)
        elif confidence != "low":
            record.emails.append(raw_email)

    if raw_person and raw_person != "null":
        record.contact_person = raw_person

    if raw_address and raw_address != "null":
        record.address = raw_address

    if raw_website and raw_website != "null":
        record.company_website = raw_website

    record.enriched_at = datetime.utcnow().isoformat() + "Z"

    if record.phone_numbers or record.emails or record.address:
        return record
    return None
