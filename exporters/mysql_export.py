"""MySQL / MariaDB exporter — for direct ingestion into the Laravel CRM database.

Schema mirrors exporters/sqlite_export.py but adds company_url, requirements/
benefits, field_sources and contact.field_sources/enrichment_sources columns
(JSON), so Gemini-sourced data ("gemini" / "gemini_description" tags) survives
into the CRM database. See docs/CRM_INTEGRATION.md for the Laravel-side setup.
"""

import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

CREATE_JOBS = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id VARCHAR(255) NOT NULL,
    source VARCHAR(64) NOT NULL,
    title VARCHAR(512),
    company VARCHAR(512),
    company_url VARCHAR(1024),
    location VARCHAR(512),
    location_city VARCHAR(255),
    location_postcode VARCHAR(32),
    salary_text VARCHAR(255),
    salary_min DOUBLE,
    salary_max DOUBLE,
    salary_period VARCHAR(32),
    job_type VARCHAR(128),
    posted_at VARCHAR(64),
    expires_at VARCHAR(64),
    apply_url VARCHAR(1024),
    description LONGTEXT,
    requirements JSON,
    benefits JSON,
    sources JSON,
    field_sources JSON,
    job_hash VARCHAR(64),
    scraped_at VARCHAR(64),
    run_id VARCHAR(64),
    PRIMARY KEY (job_id, source)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

CREATE_CONTACTS = """
CREATE TABLE IF NOT EXISTS contacts (
    company VARCHAR(512) NOT NULL PRIMARY KEY,
    phone_numbers JSON,
    emails JSON,
    contact_person VARCHAR(512),
    address VARCHAR(1024),
    website VARCHAR(1024),
    company_number VARCHAR(64),
    company_type VARCHAR(64),
    confidence_score INT,
    ai_used TINYINT(1),
    enrichment_sources JSON,
    field_sources JSON,
    enriched_at VARCHAR(64)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

CREATE_RUNS = """
CREATE TABLE IF NOT EXISTS runs (
    run_id VARCHAR(64) PRIMARY KEY,
    started_at VARCHAR(64),
    finished_at VARCHAR(64),
    jobs_scraped INT,
    jobs_duplicate INT,
    companies_enriched INT,
    ai_calls_made INT,
    errors INT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def _get_connection(config):
    try:
        import pymysql
    except ImportError as e:
        raise RuntimeError(
            "pymysql is not installed. Run:  pip install pymysql"
        ) from e

    if not config.mysql_host or not config.mysql_database:
        raise RuntimeError(
            "MySQL export requires MYSQL_HOST and MYSQL_DATABASE to be set in .env"
        )

    return pymysql.connect(
        host=config.mysql_host,
        port=getattr(config, "mysql_port", 3306) or 3306,
        user=config.mysql_user,
        password=config.mysql_password,
        database=config.mysql_database,
        charset="utf8mb4",
        autocommit=False,
    )


def init_db(config):
    conn = _get_connection(config)
    try:
        with conn.cursor() as cur:
            cur.execute(CREATE_JOBS)
            cur.execute(CREATE_CONTACTS)
            cur.execute(CREATE_RUNS)
        conn.commit()
    finally:
        conn.close()


def export_mysql(jobs: list, contacts: dict, config, run_id: str, run_stats: dict = None) -> str:
    """Write jobs + contacts to a MySQL/MariaDB database for CRM ingestion.

    Returns a descriptive string (host/database) on success, or raises on
    connection/config failure so pipeline.py can log it as an export error.
    """
    init_db(config)
    conn = _get_connection(config)

    try:
        with conn.cursor() as cur:
            for job in jobs:
                cur.execute(
                    """INSERT INTO jobs
                    (job_id, source, title, company, company_url, location, location_city,
                     location_postcode, salary_text, salary_min, salary_max, salary_period,
                     job_type, posted_at, expires_at, apply_url, description, requirements,
                     benefits, sources, field_sources, job_hash, scraped_at, run_id)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE
                        title=VALUES(title), company=VALUES(company), company_url=VALUES(company_url),
                        location=VALUES(location), location_city=VALUES(location_city),
                        location_postcode=VALUES(location_postcode), salary_text=VALUES(salary_text),
                        salary_min=VALUES(salary_min), salary_max=VALUES(salary_max),
                        salary_period=VALUES(salary_period), job_type=VALUES(job_type),
                        posted_at=VALUES(posted_at), expires_at=VALUES(expires_at),
                        apply_url=VALUES(apply_url), description=VALUES(description),
                        requirements=VALUES(requirements), benefits=VALUES(benefits),
                        sources=VALUES(sources), field_sources=VALUES(field_sources),
                        job_hash=VALUES(job_hash), scraped_at=VALUES(scraped_at), run_id=VALUES(run_id)
                    """,
                    (
                        job.job_id, job.source, job.title, job.company, job.company_url,
                        job.location, job.location_city, job.location_postcode,
                        job.salary_text, job.salary_min, job.salary_max, job.salary_period,
                        job.job_type, job.posted_at, job.expires_at, job.apply_url,
                        job.description, json.dumps(job.requirements), json.dumps(job.benefits),
                        json.dumps(job.sources or [job.source]), json.dumps(job.field_sources),
                        job._hash, job.scraped_at, run_id,
                    )
                )

            for company, contact in contacts.items():
                cur.execute(
                    """INSERT INTO contacts
                    (company, phone_numbers, emails, contact_person, address, website,
                     company_number, company_type, confidence_score, ai_used,
                     enrichment_sources, field_sources, enriched_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE
                        phone_numbers=VALUES(phone_numbers), emails=VALUES(emails),
                        contact_person=VALUES(contact_person), address=VALUES(address),
                        website=VALUES(website), company_number=VALUES(company_number),
                        company_type=VALUES(company_type), confidence_score=VALUES(confidence_score),
                        ai_used=VALUES(ai_used), enrichment_sources=VALUES(enrichment_sources),
                        field_sources=VALUES(field_sources), enriched_at=VALUES(enriched_at)
                    """,
                    (
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
                )

            stats = run_stats or {}
            cur.execute(
                """INSERT INTO runs
                (run_id, started_at, finished_at, jobs_scraped, jobs_duplicate,
                 companies_enriched, ai_calls_made, errors)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                    started_at=VALUES(started_at), finished_at=VALUES(finished_at),
                    jobs_scraped=VALUES(jobs_scraped), jobs_duplicate=VALUES(jobs_duplicate),
                    companies_enriched=VALUES(companies_enriched), ai_calls_made=VALUES(ai_calls_made),
                    errors=VALUES(errors)
                """,
                (
                    run_id,
                    stats.get("started_at", datetime.utcnow().isoformat() + "Z"),
                    stats.get("finished_at", datetime.utcnow().isoformat() + "Z"),
                    len(jobs),
                    stats.get("duplicates_removed", 0),
                    len(contacts),
                    stats.get("ai_calls", 0),
                    stats.get("errors", 0),
                )
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    target = f"{config.mysql_host}/{config.mysql_database}"
    logger.info(f"MySQL export: {len(jobs)} jobs, {len(contacts)} contacts → {target}")
    return target
