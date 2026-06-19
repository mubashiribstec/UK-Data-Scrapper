#!/usr/bin/env python3
"""UK Nurse Jobs Scraper — CLI entry point."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from utils.logger import setup_logger
from config import Config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python main.py",
        description="UK Nurse Jobs Scraper — scrapes Indeed UK and Reed.co.uk (API only)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
QUICK START
-----------
  python main.py                                     # full run, saves to output/jobs_DATE.json
  python main.py --sources indeed --max-results 10   # fast test, Indeed only
  python main.py --no-enrich                         # skip contact lookup (much faster)
  python main.py --since 7                           # jobs posted in last 7 days only

COMMON RECIPES
--------------
  # London registered nurses, JSON only:
  python main.py --keywords "registered nurse" "staff nurse" --location London

  # All sources, save everything (JSON + CSV + Excel + SQLite + MySQL/MariaDB):
  python main.py --format json csv excel sqlite mysql

  # Quick smoke test (no saving):
  python main.py --sources indeed --max-results 5 --no-enrich --dry-run

  # One-time ChatGPT/Gemini browser login (saved and reused by all later runs):
  python main.py --login-ai

  # Enable the AI pipeline (Gemini API fills in missing contact/job details):
  python main.py --ai

  # Resume last run — skip already-seen jobs:
  python main.py --resume

SOURCES
-------
  indeed     Indeed UK via browser (no login needed) — primary job source
  reed       Reed official API, ONLY used when REED_API_KEY is set
             (free key from reed.co.uk/developers); skipped entirely otherwise

OUTPUT
------
  Default format: JSON  (output/jobs_YYYY-MM-DD_HH-MM.json)
  Each run creates a new timestamped file — old files are NOT overwritten.
  See README.md for the full JSON field reference.
        """,
    )

    parser.add_argument(
        "--keywords", nargs="+", metavar="KEYWORD",
        help='Search keywords (default: nurse, registered nurse, staff nurse, community nurse, RGN, RMN, RNLD)',
    )
    parser.add_argument(
        "--location", metavar="LOCATION",
        help="Location to search (default: United Kingdom)",
    )
    parser.add_argument(
        "--max-results", type=int, metavar="N", default=None,
        help="Max results per keyword (default: 50)",
    )
    parser.add_argument(
        "--sources", nargs="+",
        choices=["reed", "indeed"],
        metavar="SOURCE",
        help="Sources to scrape: reed indeed (default: both; reed is skipped "
             "automatically if REED_API_KEY is not set)",
    )
    parser.add_argument(
        "--no-enrich", action="store_true",
        help="Skip contact enrichment — runs faster, no phone/email lookup",
    )
    parser.add_argument(
        "--ai", action="store_true",
        help="Enable AI fallback for companies with no contact data found",
    )
    parser.add_argument(
        "--ai-provider", choices=["chatgpt", "gemini-web", "gemini", "ollama", "anthropic"],
        default=None,
        help="Force one AI provider (default: automatic chain "
             "chatgpt → gemini-web → gemini → ollama → anthropic)",
    )
    parser.add_argument(
        "--login-ai", action="store_true",
        help="One-time interactive ChatGPT/Gemini browser login — opens a browser, "
             "you sign in, the session is saved and reused by all future --ai runs",
    )
    parser.add_argument(
        "--proxies", metavar="PATH", default=None,
        help="Path to a proxies file (one per line) for requests-based scrapers",
    )
    parser.add_argument(
        "--format", nargs="+",
        choices=["json", "csv", "excel", "sqlite", "mysql"],
        metavar="FORMAT",
        help="Output format(s): json csv excel sqlite mysql (default: json). "
             "'mysql' writes to a MySQL/MariaDB database configured via MYSQL_* env vars",
    )
    parser.add_argument(
        "--output-dir", metavar="PATH", default=None,
        help="Where to save output files (default: ./output)",
    )
    parser.add_argument(
        "--since", type=int, metavar="DAYS",
        help="Only include jobs posted in the last N days",
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Skip jobs already seen in a previous run (requires SQLite in output dir)",
    )
    parser.add_argument(
        "--headful", action="store_true",
        help="Show the browser window when scraping Indeed (useful for debugging)",
    )
    parser.add_argument(
        "--ai-headful", action="store_true",
        help="Show the ChatGPT/Gemini AI browser window so you can solve a "
             "'verify you are human' check (also auto-triggers when one is detected)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Run scrapers but don't save — prints a preview instead",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Show detailed per-request logging",
    )

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    log_dir = str(Path(args.output_dir or "./output") / "logs")
    setup_logger(verbose=args.verbose, log_dir=log_dir)

    import logging
    logger = logging.getLogger(__name__)

    config = Config()

    if args.keywords:
        config.keywords = args.keywords
    if args.location:
        config.locations = [args.location]
    if args.max_results is not None:
        config.max_results_per_keyword = args.max_results
    if args.no_enrich:
        config.enrich_contacts = False
    if args.ai:
        config.ai_fallback_enabled = True
    if args.ai_provider:
        config.ai_provider = args.ai_provider.replace("-", "_")
    if args.format:
        config.export_formats = args.format
    if args.output_dir:
        config.output_dir = args.output_dir
        config.sqlite_path = str(Path(args.output_dir) / "scraper.db")
    if args.headful:
        config.playwright_headless = False
    if args.ai_headful:
        config.browser_ai_headful = True
    if args.proxies:
        config.proxies_file = args.proxies

    Path(config.output_dir).mkdir(parents=True, exist_ok=True)

    if args.login_ai:
        from utils.browser_ai import run_ai_login
        ok = run_ai_login(config)
        sys.exit(0 if ok else 1)

    logger.info("UK Nurse Jobs Scraper starting")
    logger.info(
        f"keywords={config.keywords}  locations={config.locations}  "
        f"max_results={config.max_results_per_keyword}  "
        f"enrich={config.enrich_contacts}  formats={config.export_formats}"
    )

    from pipeline import run_pipeline

    try:
        result = run_pipeline(
            config=config,
            sources_filter=args.sources,
            dry_run=args.dry_run,
            resume=args.resume,
            since_days=args.since,
        )
    except KeyboardInterrupt:
        logger.warning("Interrupted — partial results may have been saved to output/")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)

    jobs = result.get("jobs", [])
    out_files = result.get("output_files", [])

    if args.dry_run:
        _print_dry_run(jobs, result.get("contacts", {}))
    elif out_files:
        print(f"\nSaved to:")
        for f in out_files:
            print(f"  {f}")
        print()

    sys.exit(0)


def _print_dry_run(jobs, contacts):
    import json as _json
    from exporters.json_export import _build_job_object

    print(f"\n{'='*60}")
    print(f"DRY RUN — {len(jobs)} jobs found (not saved)")
    print(f"{'='*60}\n")

    for job in jobs[:5]:
        obj = _build_job_object(job, contacts)
        print(_json.dumps(obj, indent=2, default=str, ensure_ascii=False))
        print()

    if len(jobs) > 5:
        print(f"  ... and {len(jobs) - 5} more jobs\n")


if __name__ == "__main__":
    main()
