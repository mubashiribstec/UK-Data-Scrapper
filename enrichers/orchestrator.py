import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Optional

from processing.merger import ContactRecord, merge_contacts
from scrapers.base import JobRecord

logger = logging.getLogger(__name__)


def _has_enough_data(record: ContactRecord) -> bool:
    return bool(record.phone_numbers) and bool(record.emails)


def _enrich_company(company: str, company_url: Optional[str], location: Optional[str], config) -> ContactRecord:
    """Run all enrichers in priority order for a single company."""
    from enrichers.website import enrich_from_website
    from enrichers.companies_house import enrich_from_companies_house
    from enrichers.charities import enrich_from_charities
    from enrichers.cqc import enrich_from_cqc
    from enrichers.duckduckgo import enrich_from_duckduckgo
    from enrichers.ai_enricher import enrich_with_ai

    collected_records = []
    merged = ContactRecord(company=company)

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
        result = enrich_from_companies_house(company, timeout=timeout)
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

    def enrich_batch(self, jobs: list[JobRecord]) -> dict[str, ContactRecord]:
        """
        Deduplicate companies first, enrich each once, return dict of company → ContactRecord.
        """
        # Gather unique companies with their representative URL and location
        companies: dict[str, tuple[Optional[str], Optional[str]]] = {}
        for job in jobs:
            name = job.company
            if not name:
                continue
            if name not in companies:
                companies[name] = (job.company_url, job.location)

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
                executor.submit(_enrich_company, name, url, loc, self.config): name
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
