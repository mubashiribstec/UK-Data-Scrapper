"""Stage 3b — mine job descriptions for structured data.

Regex-first (free): phone numbers and emails printed in the job ad itself
are the single highest-confidence contact source available.

AI second (budgeted): only for jobs where regex found nothing and structured
fields are still missing, and only when --ai is enabled.
"""

import logging
from datetime import datetime

from scrapers.base import JobRecord
from processing.cleaner import extract_phones, extract_emails, clean_phone, clean_email
from processing.merger import ContactRecord
from utils.ai_client import ask_ai, parse_ai_json, get_call_count

logger = logging.getLogger(__name__)

AI_PARSE_PROMPT = """You are a job-advert analyst. Extract structured data from this UK nursing job description.

Job title: {title}
Company: {company}

Description:
\"\"\"{description}\"\"\"

Respond ONLY with a JSON object in this exact format (use null / [] when absent):
{{
  "requirements": ["list of candidate requirements, max 8 items"],
  "benefits": ["list of benefits offered, max 8 items"],
  "phone": "phone number printed in the text or null",
  "email": "email address printed in the text or null",
  "company_name": "the employer's proper company name or null"
}}
Only include phone/email values that literally appear in the description text.
Do not output anything other than the JSON object."""


def _seed_contact(company: str, phones: list, emails: list) -> ContactRecord:
    record = ContactRecord(company=company)
    record.phone_numbers = phones
    record.emails = emails
    record.enrichment_sources = ["job_description"]
    record.enriched_at = datetime.utcnow().isoformat() + "Z"
    if phones:
        record.field_sources["phone_numbers"] = ["job_description"]
    if emails:
        record.field_sources["emails"] = ["job_description"]
    return record


def parse_jobs(jobs: list[JobRecord], config) -> dict[str, ContactRecord]:
    """Returns company → ContactRecord seeded from job description text."""
    seeds: dict[str, ContactRecord] = {}
    regex_hits = 0

    # Pass 1: regex over every description — free
    for job in jobs:
        if not job.description or not job.company:
            continue
        phones = extract_phones(job.description)
        emails = extract_emails(job.description)
        if not phones and not emails:
            continue
        regex_hits += 1
        existing = seeds.get(job.company)
        if existing:
            for p in phones:
                if p not in existing.phone_numbers:
                    existing.phone_numbers.append(p)
            for e in emails:
                if e not in existing.emails:
                    existing.emails.append(e)
        else:
            seeds[job.company] = _seed_contact(job.company, phones, emails)

    logger.info(f"Description mining: regex found contacts in {regex_hits} job ads "
                f"({len(seeds)} companies)")

    # Pass 2: AI for jobs still missing structured data — budgeted
    if not getattr(config, "ai_fallback_enabled", False):
        return seeds

    parse_limit = getattr(config, "ai_parse_limit", 30)
    ai_parsed = 0

    for job in jobs:
        if ai_parsed >= parse_limit:
            logger.info(f"Description mining: AI parse budget ({parse_limit}) reached")
            break
        if not job.description:
            continue
        company_has_contact = job.company and job.company in seeds
        needs_structure = not job.requirements or not job.benefits
        if company_has_contact and not needs_structure:
            continue
        if not needs_structure and not job.company:
            continue

        prompt = AI_PARSE_PROMPT.format(
            title=job.title or "Unknown",
            company=job.company or "Unknown",
            description=job.description[:1500],
        )
        raw = ask_ai(prompt, config, timeout=60)
        ai_parsed += 1
        if not raw:
            continue
        data = parse_ai_json(raw)
        if not data:
            continue

        reqs = data.get("requirements")
        if isinstance(reqs, list) and reqs and not job.requirements:
            job.requirements = [str(r) for r in reqs if r][:8]
            job.field_sources["requirements"] = "ai_description"

        bens = data.get("benefits")
        if isinstance(bens, list) and bens and not job.benefits:
            job.benefits = [str(b) for b in bens if b][:8]
            job.field_sources["benefits"] = "ai_description"

        # Company name normalisation: only fill a missing name, never overwrite
        norm_name = data.get("company_name")
        if norm_name and norm_name != "null" and not job.company:
            job.company = str(norm_name).strip()
            job.field_sources["company"] = "ai_description"

        if job.company:
            phone = clean_phone(data.get("phone") or "")
            email = clean_email(data.get("email") or "")
            if phone or email:
                # AI may hallucinate — accept only values literally present in the text
                in_text_phone = phone and any(
                    part in job.description for part in [phone, phone.replace(" ", "")]
                )
                in_text_email = email and email in job.description.lower()
                phones = [phone] if in_text_phone else []
                emails = [email] if in_text_email else []
                if phones or emails:
                    existing = seeds.get(job.company)
                    if existing:
                        existing.phone_numbers.extend(p for p in phones if p not in existing.phone_numbers)
                        existing.emails.extend(e for e in emails if e not in existing.emails)
                        if phones:
                            existing.field_sources.setdefault("phone_numbers", [])
                            if "job_description" not in existing.field_sources["phone_numbers"]:
                                existing.field_sources["phone_numbers"].append("job_description")
                        if emails:
                            existing.field_sources.setdefault("emails", [])
                            if "job_description" not in existing.field_sources["emails"]:
                                existing.field_sources["emails"].append("job_description")
                    else:
                        seeds[job.company] = _seed_contact(job.company, phones, emails)

    if ai_parsed:
        logger.info(f"Description mining: AI parsed {ai_parsed} job ads "
                    f"(total AI calls so far: {get_call_count()})")
    return seeds
