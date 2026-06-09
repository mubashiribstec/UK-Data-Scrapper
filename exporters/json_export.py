import json
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)


def _build_job_object(job, contacts: dict) -> dict:
    """Build a clean, spec-compliant JSON object for a single job."""
    # Contact block
    contact_record = contacts.get(job.company) if job.company else None
    if contact_record:
        contact = {
            "phone_numbers": contact_record.phone_numbers,
            "emails": contact_record.emails,
            "contact_person": contact_record.contact_person,
            "address": contact_record.address,
            "website": contact_record.company_website,
            "company_number": contact_record.company_number,
            "company_type": contact_record.company_type,
            "confidence_score": contact_record.confidence_score,
            "ai_used": contact_record.ai_used,
            "enrichment_sources": contact_record.enrichment_sources,
        }
    else:
        contact = None

    return {
        "job_id": job.job_id,
        "sources": job.sources or [job.source],
        "title": job.title,
        "company": job.company,
        "company_url": job.company_url,
        "location": job.location,
        "location_city": job.location_city,
        "location_postcode": job.location_postcode,
        "salary_text": job.salary_text,
        "salary_min": job.salary_min,
        "salary_max": job.salary_max,
        "salary_period": job.salary_period,
        "job_type": job.job_type,
        "description": job.description,
        "requirements": job.requirements,
        "benefits": job.benefits,
        "posted_at": job.posted_at,
        "expires_at": job.expires_at,
        "apply_url": job.apply_url,
        "contact": contact,
        "_hash": job._hash,
        "scraped_at": job.scraped_at,
    }


def export_json(jobs: list, contacts: dict, output_dir: str) -> str:
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    timestamp = datetime.utcnow().strftime("%Y-%m-%d_%H-%M")
    out_path = Path(output_dir) / f"jobs_{timestamp}.json"

    job_objects = [_build_job_object(job, contacts) for job in jobs]

    # Top-level envelope with run metadata
    payload = {
        "exported_at": datetime.utcnow().isoformat() + "Z",
        "total_jobs": len(job_objects),
        "total_with_contact": sum(1 for j in job_objects if j["contact"]),
        "total_with_phone": sum(
            1 for j in job_objects
            if j["contact"] and j["contact"]["phone_numbers"]
        ),
        "total_with_email": sum(
            1 for j in job_objects
            if j["contact"] and j["contact"]["emails"]
        ),
        "jobs": job_objects,
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False, default=str)

    logger.info(f"JSON export: {len(job_objects)} jobs → {out_path}")
    return str(out_path)
