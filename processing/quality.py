"""Run quality report: per-source counts, field coverage, dedup stats, provenance."""

import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

# Fields tracked for "which source supplied this data" reporting
JOB_PROVENANCE_FIELDS = [
    "title", "company", "company_url", "location", "location_city", "location_postcode",
    "salary_text", "salary_min", "salary_max", "salary_period", "job_type",
    "description", "requirements", "benefits", "posted_at", "expires_at", "apply_url",
]

CONTACT_PROVENANCE_FIELDS = [
    "phone_numbers", "emails", "contact_person", "address",
    "company_website", "company_number", "company_type",
]


def build_quality_report(jobs, contacts, per_source_raw: dict, dedup_removed: int,
                         ai_calls: int, errors: int) -> dict:
    total = len(jobs)

    per_source_unique: dict[str, int] = {}
    for job in jobs:
        for src in (job.sources or [job.source]):
            per_source_unique[src] = per_source_unique.get(src, 0) + 1

    def coverage(predicate) -> float:
        if not total:
            return 0.0
        return round(sum(1 for j in jobs if predicate(j)) / total, 3)

    def has_contact_field(job, field) -> bool:
        if not job.company:
            return False
        record = contacts.get(job.company)
        return bool(record and getattr(record, field))

    return {
        "per_source_raw": per_source_raw,
        "per_source_unique": per_source_unique,
        "field_coverage": {
            "company": coverage(lambda j: bool(j.company)),
            "location": coverage(lambda j: bool(j.location)),
            "salary": coverage(lambda j: j.salary_min is not None or bool(j.salary_text)),
            "description": coverage(lambda j: bool(j.description)),
            "posted_at": coverage(lambda j: bool(j.posted_at)),
            "requirements": coverage(lambda j: bool(j.requirements)),
            "benefits": coverage(lambda j: bool(j.benefits)),
            "phone": coverage(lambda j: has_contact_field(j, "phone_numbers")),
            "email": coverage(lambda j: has_contact_field(j, "emails")),
        },
        "dedup": {
            "raw_total": sum(per_source_raw.values()) if per_source_raw else total + dedup_removed,
            "unique": total,
            "removed": dedup_removed,
        },
        "source_attribution": build_source_attribution(jobs, contacts),
        "ai_calls": ai_calls,
        "errors": errors,
    }


def build_source_attribution(jobs, contacts) -> dict:
    """For each job/contact field, count how many records got their value from each source."""
    job_fields: dict[str, dict[str, int]] = {}
    for field_name in JOB_PROVENANCE_FIELDS:
        counts: dict[str, int] = {}
        for job in jobs:
            src = (job.field_sources or {}).get(field_name)
            if src:
                counts[src] = counts.get(src, 0) + 1
        if counts:
            job_fields[field_name] = counts

    contact_fields: dict[str, dict[str, int]] = {}
    for field_name in CONTACT_PROVENANCE_FIELDS:
        counts: dict[str, int] = {}
        for contact in contacts.values():
            src = (contact.field_sources or {}).get(field_name)
            if isinstance(src, list):
                for s in src:
                    counts[s] = counts.get(s, 0) + 1
            elif src:
                counts[src] = counts.get(src, 0) + 1
        if counts:
            contact_fields[field_name] = counts

    return {"job_fields": job_fields, "contact_fields": contact_fields}


def print_quality_report(report: dict):
    fc = report.get("field_coverage", {})
    dd = report.get("dedup", {})

    print("\n" + "=" * 60)
    print("DATA QUALITY REPORT")
    print("=" * 60)

    print("Jobs per source (raw → unique):")
    raw = report.get("per_source_raw", {})
    uniq = report.get("per_source_unique", {})
    for src in sorted(set(raw) | set(uniq)):
        print(f"  {src:<12} {raw.get(src, 0):>5} → {uniq.get(src, 0)}")

    print(f"\nDedup: {dd.get('raw_total', 0)} raw → {dd.get('unique', 0)} unique "
          f"({dd.get('removed', 0)} removed)")

    print("\nField coverage:")
    for field_name, pct in fc.items():
        bar = "█" * int(pct * 20)
        print(f"  {field_name:<14} {pct * 100:>5.1f}%  {bar}")

    print(f"\nAI calls: {report.get('ai_calls', 0)}    Errors: {report.get('errors', 0)}")

    sa = report.get("source_attribution")
    if sa:
        _print_source_attribution(sa)

    print("=" * 60 + "\n")


def _print_source_attribution(sa: dict):
    job_fields = sa.get("job_fields", {})
    contact_fields = sa.get("contact_fields", {})

    if job_fields:
        print("\nJob data — where each field came from:")
        for field_name, counts in job_fields.items():
            breakdown = ", ".join(f"{src}: {n}" for src, n in
                                   sorted(counts.items(), key=lambda kv: -kv[1]))
            print(f"  {field_name:<16} {breakdown}")

    if contact_fields:
        print("\nContact data — where each field came from:")
        for field_name, counts in contact_fields.items():
            breakdown = ", ".join(f"{src}: {n}" for src, n in
                                   sorted(counts.items(), key=lambda kv: -kv[1]))
            print(f"  {field_name:<16} {breakdown}")


def write_source_report(report: dict, output_dir: str) -> str:
    """Write a human-readable text report of which source supplied which data."""
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y-%m-%d_%H-%M")
    out_path = Path(output_dir) / f"source_report_{timestamp}.txt"

    fc = report.get("field_coverage", {})
    dd = report.get("dedup", {})
    sa = report.get("source_attribution", {})
    job_fields = sa.get("job_fields", {})
    contact_fields = sa.get("contact_fields", {})

    lines = []
    lines.append("=" * 60)
    lines.append("DATA SOURCE / PROVENANCE REPORT")
    lines.append(f"Generated: {datetime.utcnow().isoformat()}Z")
    lines.append("=" * 60)

    lines.append("\nJobs per source (raw → unique):")
    raw = report.get("per_source_raw", {})
    uniq = report.get("per_source_unique", {})
    for src in sorted(set(raw) | set(uniq)):
        lines.append(f"  {src:<12} {raw.get(src, 0):>5} -> {uniq.get(src, 0)}")
    lines.append(f"\nDedup: {dd.get('raw_total', 0)} raw -> {dd.get('unique', 0)} unique "
                  f"({dd.get('removed', 0)} removed)")

    lines.append("\nField coverage (% of jobs with this field populated):")
    for field_name, pct in fc.items():
        lines.append(f"  {field_name:<14} {pct * 100:>5.1f}%")

    lines.append("\nJob data — which source supplied each field (count of jobs):")
    if job_fields:
        for field_name, counts in job_fields.items():
            breakdown = ", ".join(f"{src}: {n}" for src, n in
                                   sorted(counts.items(), key=lambda kv: -kv[1]))
            lines.append(f"  {field_name:<16} {breakdown}")
    else:
        lines.append("  (no provenance data)")

    lines.append("\nContact data — which source supplied each field (count of companies):")
    if contact_fields:
        for field_name, counts in contact_fields.items():
            breakdown = ", ".join(f"{src}: {n}" for src, n in
                                   sorted(counts.items(), key=lambda kv: -kv[1]))
            lines.append(f"  {field_name:<16} {breakdown}")
    else:
        lines.append("  (no provenance data)")

    lines.append(f"\nAI calls: {report.get('ai_calls', 0)}    Errors: {report.get('errors', 0)}")
    lines.append("=" * 60)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    logger.info(f"Source report -> {out_path}")
    return str(out_path)
