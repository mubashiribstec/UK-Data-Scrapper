import json
import sqlite3
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

CREATE_JOBS = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT NOT NULL,
    source TEXT NOT NULL,
    title TEXT,
    company TEXT,
    location TEXT,
    location_city TEXT,
    location_postcode TEXT,
    salary_text TEXT,
    salary_min REAL,
    salary_max REAL,
    salary_period TEXT,
    job_type TEXT,
    posted_at TEXT,
    expires_at TEXT,
    apply_url TEXT,
    description TEXT,
    requirements TEXT,
    benefits TEXT,
    sources TEXT,
    _hash TEXT,
    scraped_at TEXT,
    run_id TEXT,
    PRIMARY KEY (job_id, source)
)
"""

CREATE_CONTACTS = """
CREATE TABLE IF NOT EXISTS contacts (
    company TEXT PRIMARY KEY,
    phone_numbers TEXT,
    emails TEXT,
    contact_person TEXT,
    address TEXT,
    website TEXT,
    company_number TEXT,
    company_type TEXT,
    confidence_score INTEGER,
    ai_used INTEGER,
    enrichment_sources TEXT,
    field_sources TEXT,
    enriched_at TEXT
)
"""

# Columns added after the original schema shipped — applied via ALTER TABLE so
# existing databases upgrade in place (the cache needs these to round-trip).
_CONTACTS_MIGRATIONS = [
    ("company_type", "TEXT"),
    ("field_sources", "TEXT"),
]

CREATE_RUNS = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT,
    finished_at TEXT,
    jobs_scraped INTEGER,
    jobs_new INTEGER,
    jobs_duplicate INTEGER,
    companies_enriched INTEGER,
    ai_calls_made INTEGER,
    errors INTEGER
)
"""


def _get_connection(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(db_path: str):
    conn = _get_connection(db_path)
    with conn:
        conn.execute(CREATE_JOBS)
        conn.execute(CREATE_CONTACTS)
        conn.execute(CREATE_RUNS)
        # Upgrade pre-existing contacts tables that lack the newer columns.
        for col, col_type in _CONTACTS_MIGRATIONS:
            try:
                conn.execute(f"ALTER TABLE contacts ADD COLUMN {col} {col_type}")
            except sqlite3.OperationalError:
                pass  # duplicate column — already migrated
    conn.close()


def _contact_row_params(contact) -> tuple:
    """Flatten a ContactRecord into the contacts-table column order."""
    return (
        contact.company,
        json.dumps(contact.phone_numbers),
        json.dumps(contact.emails),
        contact.contact_person,
        contact.address,
        contact.company_website,
        contact.company_number,
        contact.company_type,
        contact.confidence_score,
        1 if contact.ai_used else 0,
        json.dumps(contact.enrichment_sources),
        json.dumps(contact.field_sources),
        contact.enriched_at,
    )


_CONTACTS_INSERT = """INSERT OR REPLACE INTO contacts
    (company, phone_numbers, emails, contact_person, address,
     website, company_number, company_type, confidence_score, ai_used,
     enrichment_sources, field_sources, enriched_at)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"""


def export_sqlite(jobs: list, contacts: dict, db_path: str, run_id: str, run_stats: dict = None) -> str:
    init_db(db_path)
    conn = _get_connection(db_path)

    jobs_new = 0
    try:
        with conn:
            for job in jobs:
                try:
                    conn.execute(
                        """INSERT OR REPLACE INTO jobs
                        (job_id, source, title, company, location, location_city,
                         location_postcode, salary_text, salary_min, salary_max,
                         salary_period, job_type, posted_at, expires_at, apply_url,
                         description, requirements, benefits, sources, _hash, scraped_at, run_id)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            job.job_id, job.source, job.title, job.company,
                            job.location, job.location_city, job.location_postcode,
                            job.salary_text, job.salary_min, job.salary_max,
                            job.salary_period, job.job_type, job.posted_at,
                            job.expires_at, job.apply_url, job.description,
                            json.dumps(job.requirements), json.dumps(job.benefits),
                            json.dumps(job.sources or [job.source]),
                            job._hash, job.scraped_at, run_id,
                        )
                    )
                    jobs_new += 1
                except sqlite3.IntegrityError:
                    pass

            for company, contact in contacts.items():
                conn.execute(_CONTACTS_INSERT, _contact_row_params(contact))

            stats = run_stats or {}
            conn.execute(
                """INSERT OR REPLACE INTO runs
                (run_id, started_at, finished_at, jobs_scraped, jobs_new,
                 jobs_duplicate, companies_enriched, ai_calls_made, errors)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    run_id,
                    stats.get("started_at", datetime.utcnow().isoformat() + "Z"),
                    stats.get("finished_at", datetime.utcnow().isoformat() + "Z"),
                    len(jobs),
                    jobs_new,
                    stats.get("duplicates_removed", 0),
                    len(contacts),
                    stats.get("ai_calls", 0),
                    stats.get("errors", 0),
                )
            )
    finally:
        conn.close()

    logger.info(f"SQLite export: {len(jobs)} jobs, {len(contacts)} contacts → {db_path}")
    return db_path


def get_seen_hashes(db_path: str) -> set[str]:
    """Return set of all _hash values from previous runs (for --resume)."""
    try:
        conn = _get_connection(db_path)
        rows = conn.execute("SELECT _hash FROM jobs WHERE _hash IS NOT NULL").fetchall()
        conn.close()
        return {row[0] for row in rows}
    except Exception:
        return set()


def _json_load(raw, default):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


def get_cached_contacts(db_path: str) -> dict:
    """Load previously-enriched contacts (company -> ContactRecord) for cross-run reuse.

    Reverse of export_sqlite's contacts insert. Returns {} if the DB/table
    doesn't exist yet or on any error (first run, fresh checkout, etc.).
    """
    from processing.merger import ContactRecord
    if not Path(db_path).exists():
        return {}
    try:
        conn = _get_connection(db_path)
        try:
            rows = conn.execute("SELECT * FROM contacts").fetchall()
        finally:
            conn.close()
    except Exception:
        return {}

    cached: dict = {}
    for row in rows:
        keys = row.keys()
        record = ContactRecord(company=row["company"])
        record.phone_numbers = _json_load(row["phone_numbers"], [])
        record.emails = _json_load(row["emails"], [])
        record.contact_person = row["contact_person"]
        record.address = row["address"]
        record.company_website = row["website"]
        record.company_number = row["company_number"]
        record.company_type = row["company_type"] if "company_type" in keys else None
        record.confidence_score = row["confidence_score"] or 0
        record.ai_used = bool(row["ai_used"])
        record.enrichment_sources = _json_load(row["enrichment_sources"], [])
        if "field_sources" in keys:
            record.field_sources = _json_load(row["field_sources"], {})
        record.enriched_at = row["enriched_at"]
        cached[record.company] = record
    return cached


def upsert_contacts(db_path: str, contacts: dict, run_id: str = "") -> None:
    """Persist contacts to the cache table regardless of export format.

    Used by the always-on contact cache so repeat runs can reuse prior
    enrichment. Best-effort: never raises into the pipeline.
    """
    if not contacts:
        return
    try:
        init_db(db_path)
        conn = _get_connection(db_path)
        try:
            with conn:
                for contact in contacts.values():
                    conn.execute(_CONTACTS_INSERT, _contact_row_params(contact))
        finally:
            conn.close()
    except Exception as e:
        logger.debug(f"Contact cache upsert failed: {e}")
