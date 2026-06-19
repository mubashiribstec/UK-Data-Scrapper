import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from typing import Optional

from processing.merger import ContactRecord, merge_contacts
from scrapers.base import JobRecord

logger = logging.getLogger(__name__)


def _has_enough_data(record: ContactRecord) -> bool:
    return bool(record.phone_numbers) and bool(record.emails)


def _cache_is_fresh(cached: ContactRecord, max_age_days: int) -> bool:
    """True if the cached record has real data and isn't older than max_age_days."""
    if not (cached.phone_numbers or cached.emails or cached.address):
        return False
    if not cached.enriched_at:
        return False
    try:
        raw = cached.enriched_at.replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except Exception:
        return False
    return dt >= datetime.now(timezone.utc) - timedelta(days=max_age_days)


def _diff_against_cache(cached: ContactRecord, seed: Optional[ContactRecord]) -> dict:
    """Compare this run's freshly-scraped seed contacts to the cached values.

    Returns a changes dict {field: {"old": [...], "new": [...]}} for phone/email
    fields where the seed introduces values the cache didn't have.
    """
    changes = {}
    if not seed:
        return changes
    for field_name, cached_vals, seed_vals in (
        ("phone_numbers", cached.phone_numbers, seed.phone_numbers),
        ("emails", cached.emails, seed.emails),
    ):
        cached_set = {v.lower() for v in cached_vals}
        new_vals = [v for v in seed_vals if v.lower() not in cached_set]
        if new_vals:
            changes[field_name] = {"old": list(cached_vals), "new": list(seed_vals)}
    return changes


def _enrich_company(company: str, company_url: Optional[str], location: Optional[str], config,
                    seed: Optional[ContactRecord] = None,
                    cached: Optional[ContactRecord] = None) -> ContactRecord:
    """Run all enrichers in priority order for a single company."""
    from enrichers.website import enrich_from_website
    from enrichers.companies_house import enrich_from_companies_house
    from enrichers.charities import enrich_from_charities
    from enrichers.cqc import enrich_from_cqc
    from enrichers.duckduckgo import enrich_from_duckduckgo
    from enrichers.serpapi import enrich_from_serpapi
    from enrichers.ai_enricher import enrich_with_ai

    collected_records = []
    merged = ContactRecord(company=company)

    # -1. Cross-run cache: if this company was already enriched recently, reuse
    #     it and skip every external lookup. Still diff against the fresh seed so
    #     a changed phone/email in this run's job ad surfaces (old vs new).
    cache_days = getattr(config, "contact_cache_days", 30)
    if cached and _cache_is_fresh(cached, cache_days):
        changes = _diff_against_cache(cached, seed)
        records = [cached]
        if seed and (seed.phone_numbers or seed.emails):
            records.append(seed)
        merged = merge_contacts(records, company)
        if "cache" not in merged.enrichment_sources:
            merged.enrichment_sources.append("cache")
        merged.changes = changes
        return merged

    # 0. Contacts mined from the job description itself — highest confidence,
    #    already collected for free. May make all external lookups unnecessary.
    if seed and (seed.phone_numbers or seed.emails):
        collected_records.append(seed)
        merged = merge_contacts(collected_records, company)
        if _has_enough_data(merged):
            return merged

    timeout = getattr(config, "enrichment_timeout", 10)

    # 1. Website
    try:
        result = enrich_from_website(company, company_url=company_url, timeout=timeout)
        if result:
            collected_records.append(result)
            merged = merge_contacts(collected_records, company)
            logger.debug(f"  Website enricher found data for '{company}'")
    except Exception as e:
        logger.debug(f"Website enricher error for '{company}': {e}")

    if _has_enough_data(merged):
        return merged

    # 2. Companies House
    try:
        result = enrich_from_companies_house(
            company,
            api_key=getattr(config, "companies_house_api_key", ""),
            timeout=timeout,
        )
        if result:
            collected_records.append(result)
            merged = merge_contacts(collected_records, company)
            logger.debug(f"  Companies House enricher found data for '{company}'")
    except Exception as e:
        logger.debug(f"Companies House error for '{company}': {e}")

    if _has_enough_data(merged):
        return merged

    # 3. Charities
    try:
        result = enrich_from_charities(company, timeout=timeout)
        if result:
            collected_records.append(result)
            merged = merge_contacts(collected_records, company)
            logger.debug(f"  Charities enricher found data for '{company}'")
    except Exception as e:
        logger.debug(f"Charities enricher error for '{company}': {e}")

    if _has_enough_data(merged):
        return merged

    # 4. CQC
    try:
        result = enrich_from_cqc(company, timeout=timeout)
        if result:
            collected_records.append(result)
            merged = merge_contacts(collected_records, company)
            logger.debug(f"  CQC enricher found data for '{company}'")
    except Exception as e:
        logger.debug(f"CQC enricher error for '{company}': {e}")

    if _has_enough_data(merged):
        return merged

    # 5. DuckDuckGo
    try:
        result = enrich_from_duckduckgo(company, location=location, timeout=timeout)
        if result:
            collected_records.append(result)
            merged = merge_contacts(collected_records, company)
            logger.debug(f"  DuckDuckGo enricher found data for '{company}'")
    except Exception as e:
        logger.debug(f"DuckDuckGo enricher error for '{company}': {e}")

    if _has_enough_data(merged):
        return merged

    # 5.5 SerpAPI (paid fallback — only when DuckDuckGo found nothing/is disabled)
    if getattr(config, "serpapi_key", ""):
        try:
            result = enrich_from_serpapi(company, location=location, timeout=timeout,
                                         api_key=config.serpapi_key)
            if result:
                collected_records.append(result)
                merged = merge_contacts(collected_records, company)
                logger.debug(f"  SerpAPI enricher found data for '{company}'")
        except Exception as e:
            logger.debug(f"SerpAPI enricher error for '{company}': {e}")

    if _has_enough_data(merged):
        return merged

    # 6. AI fallback (only if enabled and still missing data)
    if getattr(config, "ai_fallback_enabled", False):
        try:
            result = enrich_with_ai(
                company,
                location=location,
                company_type=merged.company_type,
                config=config,
            )
            if result:
                collected_records.append(result)
                merged = merge_contacts(collected_records, company)
                logger.info(f"  AI enricher used for '{company}'")
        except Exception as e:
            logger.debug(f"AI enricher error for '{company}': {e}")

    if collected_records:
        return merged

    return ContactRecord(company=company)


class EnrichmentOrchestrator:
    def __init__(self, config):
        self.config = config

    def enrich_batch(self, jobs: list[JobRecord],
                     seed_contacts: dict[str, ContactRecord] = None) -> dict[str, ContactRecord]:
        """
        Deduplicate companies first, enrich each once, return dict of company → ContactRecord.
        seed_contacts: contacts already mined from job descriptions, keyed by company.
        """
        seed_contacts = seed_contacts or {}
        # Gather unique companies with their representative URL and location
        companies: dict[str, tuple[Optional[str], Optional[str]]] = {}
        for job in jobs:
            name = job.company
            if not name:
                continue
            if name not in companies:
                companies[name] = (job.company_url, job.location)

        # Cross-run cache: load previously-enriched companies so recently-fetched
        # ones can be reused instead of re-fetched (unless --fresh was passed).
        cached_contacts: dict[str, ContactRecord] = {}
        if getattr(self.config, "cache_contacts", True) and not getattr(self.config, "fresh_enrichment", False):
            try:
                from exporters.sqlite_export import get_cached_contacts
                cached_contacts = get_cached_contacts(self.config.sqlite_path)
                if cached_contacts:
                    logger.info(f"Contact cache: loaded {len(cached_contacts)} previously-enriched companies")
            except Exception as e:
                logger.debug(f"Contact cache load failed: {e}")

        logger.info(f"Enriching {len(companies)} unique companies...")

        results: dict[str, ContactRecord] = {}
        max_workers = min(4, len(companies)) if companies else 1

        try:
            from tqdm import tqdm
            progress = tqdm(total=len(companies), desc="Enriching companies", unit="company")
        except ImportError:
            progress = None

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_company = {
                executor.submit(_enrich_company, name, url, loc, self.config,
                                seed_contacts.get(name), cached_contacts.get(name)): name
                for name, (url, loc) in companies.items()
            }

            for future in as_completed(future_to_company):
                name = future_to_company[future]
                try:
                    contact = future.result(timeout=60)
                    results[name] = contact
                except Exception as e:
                    logger.error(f"Enrichment failed for '{name}': {e}")
                    results[name] = ContactRecord(company=name)
                finally:
                    if progress:
                        progress.update(1)

        if progress:
            progress.close()

        enriched_count = sum(
            1 for r in results.values()
            if r.phone_numbers or r.emails or r.address
        )
        logger.info(f"Enrichment complete: {enriched_count}/{len(companies)} companies have contact data")

        return results
