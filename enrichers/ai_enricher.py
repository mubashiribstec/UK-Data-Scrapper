import json
import re
import logging
import os
from typing import Optional
from datetime import datetime

from processing.cleaner import clean_phone, clean_email
from processing.merger import ContactRecord

logger = logging.getLogger(__name__)

_ai_call_counter = 0
AI_CALL_LIMIT = 20

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


def _parse_ai_response(text: str) -> Optional[dict]:
    """Strip markdown fences and parse JSON from AI response."""
    text = text.strip()
    # Remove markdown code fences
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try extracting JSON object from text
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except Exception:
                pass
    return None


def _call_ollama(prompt: str, model: str, base_url: str, timeout: int) -> Optional[str]:
    import requests
    try:
        resp = requests.post(
            f"{base_url}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json().get("response", "")
    except Exception as e:
        logger.warning(f"Ollama call failed: {e}")
        return None


def _call_anthropic(prompt: str, model: str, timeout: int) -> Optional[str]:
    try:
        import anthropic
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            logger.warning("AI enricher: ANTHROPIC_API_KEY not set")
            return None
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model=model,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text if message.content else None
    except Exception as e:
        logger.warning(f"Anthropic call failed: {e}")
        return None


def enrich_with_ai(
    company: str,
    location: Optional[str] = None,
    company_type: Optional[str] = None,
    config=None,
) -> Optional[ContactRecord]:
    global _ai_call_counter

    if _ai_call_counter >= AI_CALL_LIMIT:
        logger.warning(f"AI enricher: call limit ({AI_CALL_LIMIT}) reached, skipping further AI calls")
        return None

    industry_hint = company_type or "healthcare/nursing provider"
    prompt = AI_PROMPT_TEMPLATE.format(
        company_name=company,
        industry_hint=industry_hint,
        location=location or "United Kingdom",
    )

    provider = getattr(config, "ai_provider", "ollama") if config else "ollama"
    model = getattr(config, "ai_model", "llama3.2") if config else "llama3.2"
    anthropic_model = getattr(config, "anthropic_model", "claude-haiku-4-5-20251001") if config else "claude-haiku-4-5-20251001"
    ollama_base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

    _ai_call_counter += 1
    logger.info(f"AI enricher: calling {provider} for '{company}' (call #{_ai_call_counter})")

    raw_response = None
    if provider == "anthropic":
        raw_response = _call_anthropic(prompt, anthropic_model, timeout=30)
    else:
        raw_response = _call_ollama(prompt, model, ollama_base, timeout=60)

    if not raw_response:
        return None

    data = _parse_ai_response(raw_response)
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


def reset_counter():
    global _ai_call_counter
    _ai_call_counter = 0


def get_call_count() -> int:
    return _ai_call_counter
