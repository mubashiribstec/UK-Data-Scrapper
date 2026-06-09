import csv
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

JOBS_HEADERS = [
    "job_id", "sources", "title", "company", "location", "location_city",
    "location_postcode", "salary_text", "salary_min", "salary_max",
    "salary_period", "job_type", "posted_at", "expires_at", "apply_url",
    "scraped_at",
]

CONTACTS_HEADERS = [
    "company", "phone_numbers", "emails", "contact_person", "address",
    "company_website", "company_number", "company_type", "confidence_score",
    "ai_used", "enrichment_sources",
]


def export_csv(jobs: list, contacts: dict, output_dir: str) -> str:
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    date_str = datetime.utcnow().strftime("%Y-%m-%d")

    jobs_path = Path(output_dir) / f"jobs_{date_str}.csv"
    contacts_path = Path(output_dir) / f"contacts_{date_str}.csv"

    # Jobs CSV
    with open(jobs_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=JOBS_HEADERS, extrasaction="ignore")
        writer.writeheader()
        for job in jobs:
            row = job.to_dict()
            row["sources"] = "|".join(getattr(job, "_sources", [job.source]))
            writer.writerow(row)

    # Contacts CSV
    with open(contacts_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CONTACTS_HEADERS, extrasaction="ignore")
        writer.writeheader()
        for company, contact in contacts.items():
            row = contact.to_dict()
            row["phone_numbers"] = "|".join(row.get("phone_numbers", []))
            row["emails"] = "|".join(row.get("emails", []))
            row["enrichment_sources"] = "|".join(row.get("enrichment_sources", []))
            row["confidence_score"] = row.pop("contact_confidence", 0)
            writer.writerow(row)

    logger.info(f"CSV export: {len(jobs)} jobs → {jobs_path}, {len(contacts)} contacts → {contacts_path}")
    return str(jobs_path)
