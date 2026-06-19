import logging
import re
import signal
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

from config import Config
from scrapers.base import JobRecord
from processing.dedup import deduplicate
from processing.cleaner import parse_location, parse_salary
from enrichers.orchestrator import EnrichmentOrchestrator

logger = logging.getLogger(__name__)

_partial_jobs: list[JobRecord] = []
_partial_contacts: dict = {}
_interrupted = False


def _handle_interrupt(sig, frame):
    global _interrupted
    if not _interrupted:
        logger.warning("Interrupted! Saving partial results...")
    _interrupted = True


def _run_scraper(scraper_class, config, sources_filter):
    name = scraper_class.__name__.replace("Scraper", "").lower()
    if sources_filter and name.replace("_", "") not in [s.replace("_", "") for s in sources_filter]:
        logger.info(f"Skipping {name} (not in --sources filter)")
        return []
    try:
        scraper = scraper_class(config)
        return scraper.scrape_all()
    except Exception as e:
        logger.error(f"{scraper_class.__name__} failed: {e}")
        return []


def _filter_since(jobs: list, since_days: int) -> list:
    """Keep only jobs posted within the last since_days days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
    kept = []
    skipped = 0
    for job in jobs:
        if not job.posted_at:
            kept.append(job)   # keep jobs with no date (unknown)
            continue
        try:
            # Normalise various date formats to a comparable datetime
            raw = job.posted_at.strip()
            # ISO format or date-only
            raw_clean = re.sub(r"Z$", "+00:00", raw)
            if "T" in raw_clean:
                dt = datetime.fromisoformat(raw_clean)
            else:
                dt = datetime.fromisoformat(raw_clean + "T00:00:00+00:00")
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt >= cutoff:
                kept.append(job)
            else:
                skipped += 1
        except Exception:
            kept.append(job)   # unparseable date → keep it
    logger.info(f"--since {since_days}d filter: kept {len(kept)}, skipped {skipped}")
    return kept


def _clean_job(job: JobRecord, config: Config) -> JobRecord:
    """Apply cleaning passes to a single job record."""
    if job.location and (not job.location_city or not job.location_postcode):
        city, postcode = parse_location(job.location)
        if city and not job.location_city:
            job.location_city = city
            job.field_sources["location_city"] = "derived"
        if postcode and not job.location_postcode:
            job.location_postcode = postcode
            job.field_sources["location_postcode"] = "derived"

    if job.salary_text and not job.salary_min:
        sal_min, sal_max, sal_period = parse_salary(job.salary_text)
        if sal_min is not None:
            job.salary_min = sal_min
            job.field_sources["salary_min"] = "derived"
        if sal_max is not None:
            job.salary_max = sal_max
            job.field_sources["salary_max"] = "derived"
        if sal_period and not job.salary_period:
            job.salary_period = sal_period
            job.field_sources["salary_period"] = "derived"

    return job


def run_pipeline(
    config: Config,
    sources_filter: list = None,
    dry_run: bool = False,
    resume: bool = False,
    since_days: int = None,
) -> dict:
    global _partial_jobs, _partial_contacts, _interrupted
    _interrupted = False

    from utils.ai_client import reset_counter as _reset_ai
    _reset_ai()

    if getattr(config, "proxies_file", ""):
        from utils.proxy import load_proxies
        load_proxies(config.proxies_file)

    signal.signal(signal.SIGINT, _handle_interrupt)

    run_id = str(uuid.uuid4())
    started_at = datetime.utcnow().isoformat() + "Z"
    errors = 0

    logger.info(f"=== Nurse Jobs Scraper run {run_id} started at {started_at} ===")
    logger.info(f"Keywords: {config.keywords}")
    logger.info(f"Locations: {config.locations}")
    logger.info(f"Max results per keyword: {config.max_results_per_keyword}")

    # Import scrapers
    from scrapers.reed import ReedScraper
    from scrapers.indeed import IndeedScraper

    scraper_classes = [ReedScraper, IndeedScraper]

    # Stage 1: Run scrapers in parallel
    logger.info("Stage 1: Running scrapers in parallel...")
    all_raw_jobs = []
    per_source_raw: dict[str, int] = {}

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(_run_scraper, cls, config, sources_filter): cls.__name__
            for cls in scraper_classes
        }
        for future in as_completed(futures):
            if _interrupted:
                break
            name = futures[future]
            try:
                batch = future.result(timeout=600)
                all_raw_jobs.extend(batch)
                if batch:
                    per_source_raw[batch[0].source] = per_source_raw.get(batch[0].source, 0) + len(batch)
                logger.info(f"{name}: returned {len(batch)} raw jobs")
            except Exception as e:
                logger.error(f"{name} future failed: {e}")
                errors += 1

    logger.info(f"Stage 1 complete: {len(all_raw_jobs)} raw jobs collected")

    if not all_raw_jobs:
        logger.warning("No jobs collected. Check network connectivity and scraper logs.")
        return {"jobs": [], "contacts": {}, "run_id": run_id, "errors": errors}

    # Stage 2: Deduplication
    logger.info("Stage 2: Deduplicating...")
    before_dedup = len(all_raw_jobs)
    unique_jobs = deduplicate(all_raw_jobs)
    duplicates_removed = before_dedup - len(unique_jobs)
    logger.info(f"Dedup: {before_dedup} → {len(unique_jobs)} unique ({duplicates_removed} duplicates removed)")

    # Stage 2b: Resume filter (skip already-seen jobs)
    if resume:
        from exporters.sqlite_export import get_seen_hashes
        seen = get_seen_hashes(config.sqlite_path)
        before_resume = len(unique_jobs)
        unique_jobs = [j for j in unique_jobs if j._hash not in seen]
        logger.info(f"Resume: skipped {before_resume - len(unique_jobs)} already-seen jobs")

    # Stage 2c: --since filter
    if since_days:
        unique_jobs = _filter_since(unique_jobs, since_days)

    # Stage 3: Cleaning
    logger.info("Stage 3: Cleaning job records...")
    unique_jobs = [_clean_job(job, config) for job in unique_jobs]

    # Stage 3b: Mine job descriptions (regex contacts + optional AI parsing)
    logger.info("Stage 3b: Mining job descriptions...")
    seed_contacts = {}
    try:
        from processing.ai_parser import parse_jobs
        seed_contacts = parse_jobs(unique_jobs, config)
    except Exception as e:
        logger.error(f"Description mining failed: {e}")
        errors += 1

    _partial_jobs = unique_jobs

    if _interrupted:
        return _save_partial(unique_jobs, seed_contacts, run_id, started_at, errors, duplicates_removed, config, dry_run)

    # Stage 4: Contact enrichment
    contacts = seed_contacts
    if config.enrich_contacts:
        logger.info("Stage 4: Enriching contact data...")
        orchestrator = EnrichmentOrchestrator(config)
        try:
            contacts = orchestrator.enrich_batch(unique_jobs, seed_contacts=seed_contacts)
        except Exception as e:
            logger.error(f"Enrichment failed: {e}")
            errors += 1
    else:
        logger.info("Stage 4: Contact enrichment skipped (--no-enrich) — "
                    f"keeping {len(seed_contacts)} contacts mined from job descriptions")

    # Persist the contact cache regardless of export format, so future runs can
    # reuse already-fetched companies (cross-run cache).
    if getattr(config, "cache_contacts", True) and contacts:
        try:
            from exporters.sqlite_export import upsert_contacts
            upsert_contacts(config.sqlite_path, contacts, run_id)
        except Exception as e:
            logger.debug(f"Contact cache persist failed: {e}")

    _partial_contacts = contacts

    if _interrupted:
        return _save_partial(unique_jobs, contacts, run_id, started_at, errors, duplicates_removed, config, dry_run)

    # Stage 5: Export
    from utils.ai_client import get_call_count
    from processing.quality import build_quality_report, print_quality_report
    ai_calls = get_call_count()
    finished_at = datetime.utcnow().isoformat() + "Z"

    quality_report = build_quality_report(
        unique_jobs, contacts, per_source_raw, duplicates_removed, ai_calls, errors
    )

    run_stats = {
        "started_at": started_at,
        "finished_at": finished_at,
        "duplicates_removed": duplicates_removed,
        "ai_calls": ai_calls,
        "errors": errors,
        "quality_report": quality_report,
    }

    if dry_run:
        logger.info("=== DRY RUN — results not saved ===")
        _print_summary(unique_jobs, contacts, run_stats)
        return {"jobs": unique_jobs, "contacts": contacts, "run_id": run_id, "output_files": [], **run_stats}

    logger.info("Stage 5: Exporting results...")
    out_files = _export_all(unique_jobs, contacts, config, run_id, run_stats)

    _print_summary(unique_jobs, contacts, run_stats)

    return {
        "jobs": unique_jobs,
        "contacts": contacts,
        "run_id": run_id,
        "output_files": out_files,
        **run_stats,
    }


def _export_all(jobs, contacts, config, run_id, run_stats) -> list:
    """Run all configured exporters and return list of written file paths."""
    formats = config.export_formats
    from pathlib import Path
    Path(config.output_dir).mkdir(parents=True, exist_ok=True)

    out_files = []

    if "json" in formats:
        from exporters.json_export import export_json
        path = export_json(jobs, contacts, config.output_dir,
                           quality_report=run_stats.get("quality_report"))
        if path:
            out_files.append(path)

    if "csv" in formats:
        from exporters.csv_export import export_csv
        path = export_csv(jobs, contacts, config.output_dir)
        if path:
            out_files.append(path)

    if "excel" in formats:
        from exporters.excel_export import export_excel
        path = export_excel(jobs, contacts, config.output_dir, run_stats=run_stats)
        if path:
            out_files.append(path)

    if "sqlite" in formats:
        from exporters.sqlite_export import export_sqlite
        path = export_sqlite(jobs, contacts, config.sqlite_path, run_id, run_stats=run_stats)
        if path:
            out_files.append(path)

    if "mysql" in formats:
        from exporters.mysql_export import export_mysql
        try:
            target = export_mysql(jobs, contacts, config, run_id, run_stats=run_stats)
            out_files.append(f"mysql:{target}")
        except Exception as e:
            logger.error(f"MySQL export failed: {e}")

    if run_stats.get("quality_report"):
        from processing.quality import write_source_report
        path = write_source_report(run_stats["quality_report"], config.output_dir)
        if path:
            out_files.append(path)

    return out_files


def _save_partial(jobs, contacts, run_id, started_at, errors, duplicates_removed, config, dry_run):
    if dry_run or not jobs:
        return {"jobs": jobs, "contacts": contacts, "run_id": run_id, "output_files": []}
    logger.info(f"Saving {len(jobs)} partial results before exit...")
    from utils.ai_client import get_call_count
    run_stats = {
        "started_at": started_at,
        "finished_at": datetime.utcnow().isoformat() + "Z",
        "duplicates_removed": duplicates_removed,
        "ai_calls": get_call_count(),
        "errors": errors,
        "partial": True,
    }
    out_files = _export_all(jobs, contacts, config, run_id, run_stats)
    return {"jobs": jobs, "contacts": contacts, "run_id": run_id, "output_files": out_files, **run_stats}


def _print_summary(jobs, contacts, run_stats):
    ai_calls = run_stats.get("ai_calls", 0)
    jobs_with_phone = sum(1 for j in jobs if j.company and contacts.get(j.company) and contacts[j.company].phone_numbers)
    jobs_with_email = sum(1 for j in jobs if j.company and contacts.get(j.company) and contacts[j.company].emails)

    print("\n" + "=" * 60)
    print("SCRAPER RUN SUMMARY")
    print("=" * 60)
    print(f"Total unique jobs:        {len(jobs)}")
    print(f"Companies enriched:       {len(contacts)}")
    print(f"Jobs with phone number:   {jobs_with_phone}")
    print(f"Jobs with email:          {jobs_with_email}")
    print(f"AI calls made:            {ai_calls}")
    print(f"Errors:                   {run_stats.get('errors', 0)}")
    print("=" * 60 + "\n")

    if run_stats.get("quality_report"):
        from processing.quality import print_quality_report
        print_quality_report(run_stats["quality_report"])
