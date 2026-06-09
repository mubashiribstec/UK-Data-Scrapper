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
    confidence_score INTEGER,
    ai_used INTEGER,
    enrichment_sources TEXT,
    enriched_at TEXT
)
"""

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
    conn.close()


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
                         description, requirements, _hash, scraped_at, run_id)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            job.job_id, job.source, job.title, job.company,
                            job.location, job.location_city, job.location_postcode,
                            job.salary_text, job.salary_min, job.salary_max,
                            job.salary_period, job.job_type, job.posted_at,
                            job.expires_at, job.apply_url, job.description,
                            json.dumps(job.requirements), job._hash,
                            job.scraped_at, run_id,
                        )
                    )
                    jobs_new += 1
                except sqlite3.IntegrityError:
                    pass

            for company, contact in contacts.items():
                conn.execute(
                    """INSERT OR REPLACE INTO contacts
                    (company, phone_numbers, emails, contact_person, address,
                     website, company_number, confidence_score, ai_used,
                     enrichment_sources, enriched_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        contact.company,
                        json.dumps(contact.phone_numbers),
                        json.dumps(contact.emails),
                        contact.contact_person,
                        contact.address,
                        contact.company_website,
                        contact.company_number,
                        contact.contact_confidence,
                        1 if contact.ai_used else 0,
                        json.dumps(contact.enrichment_sources),
                        contact.enriched_at,
                    )
                )

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
