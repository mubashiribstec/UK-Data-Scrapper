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

# Job fields tracked for source-provenance reporting
_TRACKED_FIELDS = [
    "title", "company", "company_url", "location", "location_city", "location_postcode",
    "salary_text", "salary_min", "salary_max", "salary_period", "job_type",
    "description", "requirements", "benefits", "posted_at", "expires_at", "apply_url",
]


def _init_field_sources(job: JobRecord) -> dict:
    """Map each populated field on a freshly-scraped job to its origin source."""
    return {
        f: job.source
        for f in _TRACKED_FIELDS
        if getattr(job, f) not in (None, "", [])
    }


def _normalise(text: str) -> str:
    if not text:
        return ""
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    for suffix in _STOP_SUFFIXES:
        text = re.sub(r"\b" + re.escape(suffix) + r"\b", "", text)
    return text.strip()


def _content_hash(title: str, company: str, location: str, job_id: str = "") -> str:
    t, c, l = _normalise(title), _normalise(company), _normalise(location)
    # When all three fields are empty, fall back to job_id to avoid false collapse
    if not t and not c and not l:
        key = f"__fallback__|{job_id}"
    else:
        key = f"{t}|{c}|{l}"
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
        h = _content_hash(job.title or "", job.company or "", job.location or "", job.job_id or "")
        job._hash = h

        if h in seen_hashes:
            existing = seen_hashes[h]
            merged = _merge_richer(existing, job)
            if not merged.sources:
                merged.sources = [existing.source]
            if job.source not in merged.sources:
                merged.sources.append(job.source)
            seen_hashes[h] = merged
            if merged is not existing:
                # _merge_richer picked the new job as the richer record —
                # swap it into `result` so the merge isn't silently dropped.
                for idx, r in enumerate(result):
                    if r is existing:
                        result[idx] = merged
                        break
            duplicate_count += 1
            continue

        job.sources = [job.source]
        job.field_sources = _init_field_sources(job)
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

    if not primary.field_sources:
        primary.field_sources = _init_field_sources(primary)
    if not secondary.field_sources:
        secondary.field_sources = _init_field_sources(secondary)

    for field_name in [
        "description", "salary_text", "salary_min", "salary_max", "salary_period",
        "job_type", "company_url", "location_city", "location_postcode",
        "posted_at", "expires_at", "apply_url"
    ]:
        if getattr(primary, field_name) is None:
            value = getattr(secondary, field_name)
            if value is not None:
                setattr(primary, field_name, value)
                primary.field_sources[field_name] = secondary.field_sources.get(field_name, secondary.source)

    if not primary.requirements and secondary.requirements:
        primary.requirements = secondary.requirements
        primary.field_sources["requirements"] = secondary.field_sources.get("requirements", secondary.source)
    if not primary.benefits and secondary.benefits:
        primary.benefits = secondary.benefits
        primary.field_sources["benefits"] = secondary.field_sources.get("benefits", secondary.source)

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
