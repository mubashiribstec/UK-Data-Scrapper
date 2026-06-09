"""Run quality report: per-source counts, field coverage, dedup stats."""

import logging

logger = logging.getLogger(__name__)


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
        "ai_calls": ai_calls,
        "errors": errors,
    }


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
    print("=" * 60 + "\n")
