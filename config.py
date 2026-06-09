import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    # Search parameters
    keywords: list = field(default_factory=lambda: [
        "nurse", "registered nurse", "staff nurse",
        "community nurse", "RGN", "RMN", "RNLD"
    ])
    locations: list = field(default_factory=lambda: [
        "United Kingdom"
    ])
    max_results_per_keyword: int = 50

    # Scraper behaviour
    request_delay_min: float = 2.0
    request_delay_max: float = 5.0
    request_timeout: int = 15
    max_retries: int = 3
    playwright_headless: bool = True

    # Enrichment
    enrich_contacts: bool = True
    enrichment_timeout: int = 10
    ai_fallback_enabled: bool = False
    ai_provider: str = "ollama"
    ai_model: str = "llama3.2"
    anthropic_model: str = "claude-haiku-4-5-20251001"

    # Output
    output_dir: str = "./output"
    export_formats: list = field(default_factory=lambda: ["json"])
    sqlite_path: str = "./output/scraper.db"

    # Rate limiting per domain
    domain_delays: dict = field(default_factory=lambda: {
        "uk.indeed.com": 4.0,
        "www.reed.co.uk": 2.5,
        "api.jobs.nhs.uk": 1.0,
        "api.company-information.service.gov.uk": 0.5
    })

    def __post_init__(self):
        # Load overrides from environment variables
        if os.getenv("MAX_RESULTS_PER_KEYWORD"):
            self.max_results_per_keyword = int(os.getenv("MAX_RESULTS_PER_KEYWORD"))
        if os.getenv("REQUEST_DELAY_MIN"):
            self.request_delay_min = float(os.getenv("REQUEST_DELAY_MIN"))
        if os.getenv("REQUEST_DELAY_MAX"):
            self.request_delay_max = float(os.getenv("REQUEST_DELAY_MAX"))
        if os.getenv("AI_FALLBACK_ENABLED"):
            self.ai_fallback_enabled = os.getenv("AI_FALLBACK_ENABLED").lower() == "true"
        if os.getenv("AI_PROVIDER"):
            self.ai_provider = os.getenv("AI_PROVIDER")
        if os.getenv("AI_MODEL"):
            self.ai_model = os.getenv("AI_MODEL")
        if os.getenv("OUTPUT_DIR"):
            self.output_dir = os.getenv("OUTPUT_DIR")
        if os.getenv("SQLITE_PATH"):
            self.sqlite_path = os.getenv("SQLITE_PATH")
        if os.getenv("PLAYWRIGHT_HEADLESS"):
            self.playwright_headless = os.getenv("PLAYWRIGHT_HEADLESS").lower() != "false"
        if os.getenv("ENRICH_CONTACTS"):
            self.enrich_contacts = os.getenv("ENRICH_CONTACTS").lower() == "true"
