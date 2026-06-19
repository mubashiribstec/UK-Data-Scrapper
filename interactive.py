#!/usr/bin/env python3
"""UK Nurse Jobs Scraper — interactive wizard.

Walks you through picking keywords, sources, and whether to use AI to
fill in missing fields, then runs the full pipeline and points you at
the data-provenance report (which source supplied which piece of data).

Usage:
    python interactive.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config import Config
from utils.logger import setup_logger

ALL_SOURCES = ["reed", "indeed"]

SOURCE_LABELS = {
    "reed": "Reed.co.uk (official API, requires REED_API_KEY)",
    "indeed": "Indeed UK",
}


def _prompt_keywords(default: list) -> list:
    print("\nKeywords to search for (comma-separated).")
    print(f"  Default: {', '.join(default)}")
    raw = input("> ").strip()
    if not raw:
        print(f"Using default keywords: {', '.join(default)}")
        return default
    keywords = [k.strip() for k in raw.split(",") if k.strip()]
    return keywords or default


def _prompt_sources() -> list:
    print("\nWhich job sources should be scraped?")
    for i, src in enumerate(ALL_SOURCES, 1):
        print(f"  {i}. {src:<10} {SOURCE_LABELS[src]}")
    print("Enter numbers or names, comma-separated (e.g. '1,2' or 'reed,indeed').")
    print("  Default: all sources")
    raw = input("> ").strip()
    if not raw:
        print("Using all sources")
        return list(ALL_SOURCES)

    chosen = []
    for token in raw.split(","):
        token = token.strip().lower()
        if not token:
            continue
        if token.isdigit() and 1 <= int(token) <= len(ALL_SOURCES):
            chosen.append(ALL_SOURCES[int(token) - 1])
        elif token in ALL_SOURCES:
            chosen.append(token)
        else:
            print(f"  (ignoring unrecognised source '{token}')")

    if not chosen:
        print("No valid sources recognised — using all sources")
        return list(ALL_SOURCES)

    seen = set()
    result = []
    for s in chosen:
        if s not in seen:
            seen.add(s)
            result.append(s)
    return result


def _prompt_ai(config) -> bool:
    print("\nUse AI (Gemini API, with Ollama/Anthropic failover) to fill in missing fields")
    print("(requirements, benefits, phone, email) when a source doesn't provide them?")
    raw = input("Enable AI fallback? [y/N] > ").strip().lower()
    if raw not in ("y", "yes"):
        return False

    if not config.gemini_api_key:
        print("\nNo GEMINI_API_KEY found in your environment / .env file.")
        print("Set GEMINI_API_KEY in .env to enable AI lookups (free key from "
              "aistudio.google.com/app/apikey).")
        print("AI will be skipped at runtime until a provider is configured.")
    return True


def main():
    print("=" * 60)
    print("UK NURSE JOBS SCRAPER — Interactive Setup")
    print("=" * 60)

    config = Config()

    config.keywords = _prompt_keywords(config.keywords)
    sources = _prompt_sources()

    if _prompt_ai(config):
        config.ai_fallback_enabled = True
        print("AI fallback enabled.")
    else:
        print("AI fallback disabled — only free regex-based contact mining from job descriptions will run.")

    Path(config.output_dir).mkdir(parents=True, exist_ok=True)
    setup_logger(verbose=False, log_dir=str(Path(config.output_dir) / "logs"))

    print("\nStarting scrape with:")
    print(f"  Keywords:    {config.keywords}")
    print(f"  Sources:     {sources}")
    print(f"  AI fallback: {config.ai_fallback_enabled}")
    print()

    from pipeline import run_pipeline
    result = run_pipeline(config, sources_filter=sources)

    out_files = result.get("output_files", [])
    if out_files:
        print("\nSaved to:")
        for f in out_files:
            print(f"  {f}")

        report_files = [f for f in out_files if "source_report" in f]
        if report_files:
            print(f"\nData provenance report: {report_files[0]}")
            print("(shows which source supplied each field — job listings, salary, contacts, etc.)")


if __name__ == "__main__":
    main()
