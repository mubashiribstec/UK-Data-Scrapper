#!/usr/bin/env python3
"""UK Nurse Jobs Scraper — CLI entry point."""

import argparse
import sys
import os
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).parent))

from utils.logger import setup_logger
from config import Config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="UK Nurse Jobs Scraper — scrapes NHS Jobs, Reed, and Indeed UK",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py
  python main.py --keywords "registered nurse" "RMN" --location London
  python main.py --sources nhs reed --no-enrich --format csv
  python main.py --ai --ai-provider ollama --max-results 100
  python main.py --since 7 --format excel
  python main.py --sources nhs --max-results 5 --no-enrich --dry-run
        """,
    )

    parser.add_argument(
        "--keywords", nargs="+", metavar="KEYWORD",
        help="Override config keywords",
    )
    parser.add_argument(
        "--location", metavar="LOCATION",
        help="Override config location (single location)",
    )
    parser.add_argument(
        "--max-results", type=int, metavar="N", default=None,
        help="Max results per keyword (default: 50)",
    )
    parser.add_argument(
        "--sources", nargs="+", choices=["nhs", "reed", "indeed"],
        metavar="SOURCE",
        help="Which sources to use (default: all). Choices: nhs reed indeed",
    )
    parser.add_argument(
        "--no-enrich", action="store_true",
        help="Skip contact enrichment (faster)",
    )
    parser.add_argument(
        "--ai", action="store_true",
        help="Enable AI fallback enrichment",
    )
    parser.add_argument(
        "--ai-provider", choices=["ollama", "anthropic"], default=None,
        help="AI provider to use (default: ollama)",
    )
    parser.add_argument(
        "--format", nargs="+",
        choices=["json", "csv", "excel", "sqlite"],
        metavar="FORMAT",
        help="Output format(s) (default: all). Choices: json csv excel sqlite",
    )
    parser.add_argument(
        "--output-dir", metavar="PATH",
        help="Output directory (default: ./output)",
    )
    parser.add_argument(
        "--headful", action="store_true",
        help="Run Playwright visibly (debug mode)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Scrape only, print results, don't save",
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Resume from last run — skip already-seen jobs",
    )
    parser.add_argument(
        "--since", type=int, metavar="DAYS",
        help="Only get jobs posted in the last N days (not yet fully implemented — note in logs)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Verbose logging",
    )

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    # Setup logging first
    log_dir = "./output/logs"
    if args.output_dir:
        log_dir = str(Path(args.output_dir) / "logs")
    setup_logger(verbose=args.verbose, log_dir=log_dir)

    import logging
    logger = logging.getLogger(__name__)

    # Build config with overrides
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
        config.ai_provider = args.ai_provider
    if args.format:
        config.export_formats = args.format
    if args.output_dir:
        config.output_dir = args.output_dir
        config.sqlite_path = str(Path(args.output_dir) / "scraper.db")
    if args.headful:
        config.playwright_headless = False

    if args.since:
        logger.info(f"--since {args.since}: date filtering will be applied at export stage")

    # Create output directory
    Path(config.output_dir).mkdir(parents=True, exist_ok=True)

    logger.info("Starting UK Nurse Jobs Scraper")
    logger.info(f"Config: keywords={config.keywords}, locations={config.locations}, "
                f"max_results={config.max_results_per_keyword}, enrich={config.enrich_contacts}")

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
        logger.warning("Interrupted by user. Partial results may have been saved.")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Pipeline failed with unexpected error: {e}", exc_info=True)
        sys.exit(1)

    jobs = result.get("jobs", [])
    if args.dry_run and jobs:
        print(f"\nDRY RUN: {len(jobs)} jobs found. First 5:\n")
        for job in jobs[:5]:
            print(f"  [{job.source}] {job.title} @ {job.company} — {job.location}")
            if job.salary_text:
                print(f"           Salary: {job.salary_text}")
            print(f"           Apply: {job.apply_url}")
            print()

    sys.exit(0)


if __name__ == "__main__":
    main()
