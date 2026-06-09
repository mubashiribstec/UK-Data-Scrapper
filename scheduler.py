#!/usr/bin/env python3
"""
Cron-compatible scheduler wrapper for the UK Nurse Jobs Scraper.

Cron setup (weekly Monday 6am):
  0 6 * * 1 cd /home/ubuntu/nurse_scraper && python scheduler.py >> /var/log/nurse_scraper.log 2>&1
"""

import fcntl
import logging
import os
import smtplib
import sys
import traceback
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path

LOCK_FILE = "/tmp/nurse_scraper.lock"
LOG_DIR = "./output/logs"
ERROR_DIR = "./output/errors"


def _acquire_lock():
    lock_fp = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return lock_fp
    except IOError:
        print(f"Another instance is already running (lock: {LOCK_FILE}). Exiting.")
        sys.exit(0)


def _release_lock(lock_fp):
    try:
        fcntl.flock(lock_fp, fcntl.LOCK_UN)
        lock_fp.close()
        os.unlink(LOCK_FILE)
    except Exception:
        pass


def _send_email_summary(subject: str, body: str):
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT", 587))
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASS")
    notify_email = os.getenv("NOTIFY_EMAIL")

    if not all([smtp_host, smtp_user, smtp_pass, notify_email]):
        return

    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = smtp_user
        msg["To"] = notify_email

        with smtplib.SMTP(smtp_host, smtp_port) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(smtp_user, smtp_pass)
            smtp.send_message(msg)
        logging.info(f"Email summary sent to {notify_email}")
    except Exception as e:
        logging.warning(f"Failed to send email: {e}")


def main():
    Path(LOG_DIR).mkdir(parents=True, exist_ok=True)
    Path(ERROR_DIR).mkdir(parents=True, exist_ok=True)

    lock_fp = _acquire_lock()
    run_date = datetime.utcnow().strftime("%Y-%m-%d")

    from utils.logger import setup_logger
    setup_logger(verbose=False, log_dir=LOG_DIR)
    logger = logging.getLogger(__name__)

    logger.info(f"Scheduler: starting run for {run_date}")

    try:
        from config import Config
        from pipeline import run_pipeline

        config = Config()
        result = run_pipeline(config=config)

        jobs = result.get("jobs", [])
        contacts = result.get("contacts", {})
        ai_calls = result.get("ai_calls", 0)
        errors = result.get("errors", 0)

        subject = f"Nurse Jobs Scraper: {len(jobs)} jobs scraped on {run_date}"
        body = (
            f"Run completed successfully.\n\n"
            f"Total jobs: {len(jobs)}\n"
            f"Companies enriched: {len(contacts)}\n"
            f"AI calls: {ai_calls}\n"
            f"Errors: {errors}\n\n"
            f"Results saved to: {config.output_dir}\n"
        )
        _send_email_summary(subject, body)
        logger.info("Scheduler: run completed successfully")

    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"Scheduler: run failed: {e}\n{tb}")

        error_path = Path(ERROR_DIR) / f"error_{run_date}.txt"
        error_path.write_text(f"Error on {run_date}:\n{tb}")

        _send_email_summary(
            f"Nurse Jobs Scraper ERROR on {run_date}",
            f"The scraper failed with the following error:\n\n{tb}"
        )
    finally:
        _release_lock(lock_fp)


if __name__ == "__main__":
    main()
