import logging
from dataclasses import dataclass, field
from typing import Optional
from processing.cleaner import sort_emails_by_priority

logger = logging.getLogger(__name__)


@dataclass
class ContactRecord:
    company: str
    phone_numbers: list = field(default_factory=list)
    emails: list = field(default_factory=list)
    contact_person: Optional[str] = None
    address: Optional[str] = None
    company_website: Optional[str] = None
    company_number: Optional[str] = None
    company_type: Optional[str] = None
    enrichment_sources: list = field(default_factory=list)
    ai_used: bool = False
    enriched_at: Optional[str] = None
    confidence_score: int = 0
    field_sources: dict = field(default_factory=dict)  # field name -> origin source(s)

    def to_dict(self) -> dict:
        return {
            "company": self.company,
            "phone_numbers": self.phone_numbers,
            "emails": self.emails,
            "contact_person": self.contact_person,
            "address": self.address,
            "company_website": self.company_website,
            "company_number": self.company_number,
            "company_type": self.company_type,
            "enrichment_sources": self.enrichment_sources,
            "ai_used": self.ai_used,
            "enriched_at": self.enriched_at,
            "confidence_score": self.confidence_score,
            "field_sources": self.field_sources,
        }


def merge_contacts(records: list[ContactRecord], company: str) -> ContactRecord:
    """Merge multiple ContactRecords into one, following priority rules."""
    if not records:
        return ContactRecord(company=company)

    merged = ContactRecord(company=company)

    def src_of(r: ContactRecord) -> str:
        return r.enrichment_sources[0] if r.enrichment_sources else "unknown"

    # Phone numbers: union of all valid, deduplicated
    seen_phones = set()
    phone_sources = []
    for r in records:
        for p in r.phone_numbers:
            if p and p not in seen_phones:
                seen_phones.add(p)
                merged.phone_numbers.append(p)
                if src_of(r) not in phone_sources:
                    phone_sources.append(src_of(r))
    if phone_sources:
        merged.field_sources["phone_numbers"] = phone_sources

    # Emails: union, deduped, sorted with hr@ first
    seen_emails = set()
    all_emails = []
    email_sources = []
    for r in records:
        for e in r.emails:
            if e and e.lower() not in seen_emails:
                seen_emails.add(e.lower())
                all_emails.append(e)
                if src_of(r) not in email_sources:
                    email_sources.append(src_of(r))
    merged.emails = sort_emails_by_priority(all_emails)
    if email_sources:
        merged.field_sources["emails"] = email_sources

    # Contact person: first non-null priority order (job ad itself > website > CH > AI)
    source_priority = ["job_description", "website", "companies_house", "charities", "cqc", "duckduckgo", "ai"]
    records_by_source = {r.enrichment_sources[0] if r.enrichment_sources else "unknown": r for r in records}
    for src in source_priority:
        if src in records_by_source and records_by_source[src].contact_person:
            merged.contact_person = records_by_source[src].contact_person
            merged.field_sources["contact_person"] = src
            break

    # Address: prefer Companies House > CQC > website
    for src in ["companies_house", "cqc", "charities", "website", "job_description", "duckduckgo", "ai"]:
        if src in records_by_source and records_by_source[src].address:
            merged.address = records_by_source[src].address
            merged.field_sources["address"] = src
            break

    # Website: prefer https, prefer known domains
    for r in records:
        if r.company_website:
            if not merged.company_website:
                merged.company_website = r.company_website
                merged.field_sources["company_website"] = src_of(r)
            elif r.company_website.startswith("https") and not merged.company_website.startswith("https"):
                merged.company_website = r.company_website
                merged.field_sources["company_website"] = src_of(r)

    # Company number from Companies House only
    for r in records:
        if r.company_number:
            merged.company_number = r.company_number
            merged.field_sources["company_number"] = src_of(r)
            break

    # Company type
    for r in records:
        if r.company_type:
            merged.company_type = r.company_type
            merged.field_sources["company_type"] = src_of(r)
            break

    # Enrichment sources
    all_sources = []
    for r in records:
        for s in r.enrichment_sources:
            if s not in all_sources:
                all_sources.append(s)
    merged.enrichment_sources = all_sources

    # AI used flag
    merged.ai_used = any(r.ai_used for r in records)

    # Enriched at: latest timestamp
    timestamps = [r.enriched_at for r in records if r.enriched_at]
    merged.enriched_at = max(timestamps) if timestamps else None

    # Confidence score
    merged.confidence_score = _compute_confidence(merged)

    return merged


def _compute_confidence(record: ContactRecord) -> int:
    score = 0
    if record.phone_numbers:
        score += 30
    if record.emails:
        score += 30
    if record.company_number:
        score += 20
    if record.contact_person:
        score += 10
    if record.company_website:
        score += 10
    if record.ai_used and len(record.enrichment_sources) == 1:
        score -= 20
    # Contact details printed in the job advert itself are the strongest signal
    if "job_description" in record.enrichment_sources:
        score += 10
    return max(0, min(100, score))
