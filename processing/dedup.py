import re
import hashlib
import logging
from difflib import SequenceMatcher
from scrapers.base import JobRecord

logger = logging.getLogger(__name__)

_STOP_SUFFIXES = [
    "nhs foundation trust", "nhs trust", "foundation trust",
    "ltd", "limited", "plc", "llp", "llc",
]


def _normalise(text: str) -> str:
    if not text:
        return ""
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    for suffix in _STOP_SUFFIXES:
        text = re.sub(r"\b" + re.escape(suffix) + r"\b", "", text)
    return text.strip()


def _content_hash(title: str, company: str, location: str) -> str:
    key = f"{_normalise(title)}|{_normalise(company)}|{_normalise(location)}"
    return hashlib.md5(key.encode()).hexdigest()[:12]


def deduplicate(jobs: list[JobRecord]) -> list[JobRecord]:
    """Three-level deduplication: source ID, content hash, fuzzy title."""
    seen_source_ids: dict[str, set] = {}
    seen_hashes: dict[str, JobRecord] = {}
    result: list[JobRecord] = []
    duplicate_count = 0

    for job in jobs:
        # Level 1: exact source ID dedup
        source_ids = seen_source_ids.setdefault(job.source, set())
        if job.job_id in source_ids:
            duplicate_count += 1
            continue
        source_ids.add(job.job_id)

        # Level 2: cross-source content hash
        h = _content_hash(job.title or "", job.company or "", job.location or "")
        job._hash = h

        if h in seen_hashes:
            existing = seen_hashes[h]
            merged = _merge_richer(existing, job)
            if not merged.sources:
                merged.sources = [existing.source]
            if job.source not in merged.sources:
                merged.sources.append(job.source)
            seen_hashes[h] = merged
            duplicate_count += 1
            continue

        job.sources = [job.source]
        seen_hashes[h] = job
        result.append(job)

    # Level 3: fuzzy title match within same company
    result = _fuzzy_dedup(result)

    # Ensure all jobs have sources populated
    for job in result:
        if not job.sources:
            job.sources = [job.source]

    logger.info(f"Dedup: {len(jobs)} → {len(result)} unique ({duplicate_count} removed)")
    return result


def _merge_richer(a: JobRecord, b: JobRecord) -> JobRecord:
    """Return the record with more non-null fields, filling gaps from the other."""
    def score(j: JobRecord) -> int:
        return sum(1 for v in j.to_dict().values() if v is not None and v != [] and v != "")

    primary, secondary = (a, b) if score(a) >= score(b) else (b, a)

    for field_name in [
        "description", "salary_text", "salary_min", "salary_max", "salary_period",
        "job_type", "company_url", "location_city", "location_postcode",
        "posted_at", "expires_at", "apply_url"
    ]:
        if getattr(primary, field_name) is None:
            setattr(primary, field_name, getattr(secondary, field_name))

    if not primary.requirements:
        primary.requirements = secondary.requirements
    if not primary.benefits:
        primary.benefits = secondary.benefits

    return primary


def _fuzzy_dedup(jobs: list[JobRecord]) -> list[JobRecord]:
    """Remove near-duplicate titles within the same company."""
    result = []
    for job in jobs:
        is_dup = False
        for existing in result:
            if (existing.company or "").lower() != (job.company or "").lower():
                continue
            sim = SequenceMatcher(
                None,
                _normalise(existing.title or ""),
                _normalise(job.title or "")
            ).ratio()
            if sim >= 0.85:
                # Keep the richer one
                merged = _merge_richer(existing, job)
                idx = result.index(existing)
                result[idx] = merged
                is_dup = True
                break
        if not is_dup:
            result.append(job)
    return result
