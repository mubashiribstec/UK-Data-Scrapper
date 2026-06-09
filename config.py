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
    ai_provider: str = ""               # "" = automatic chain (gemini → ollama → anthropic)
    ai_model: str = "llama3.2"          # Ollama model name
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"
    ollama_base_url: str = "http://localhost:11434"
    anthropic_model: str = "claude-haiku-4-5-20251001"
    ai_call_limit: int = 20             # max AI contact-enrichment calls per run
    ai_parse_limit: int = 30            # max AI description-parsing calls per run

    # Source credentials / keys
    reed_api_key: str = ""              # free key from reed.co.uk/developers
    indeed_profile_dir: str = "./output/.browser/indeed"

    # Proxies (optional, requests-based scrapers only)
    proxies_file: str = ""

    # Output
    output_dir: str = "./output"
    export_formats: list = field(default_factory=lambda: ["json"])
    sqlite_path: str = "./output/scraper.db"

    # Rate limiting per domain
    domain_delays: dict = field(default_factory=lambda: {
        "uk.indeed.com": 4.0,
        "www.reed.co.uk": 2.5,
        "api.jobs.nhs.uk": 1.0,
        "www.totaljobs.com": 3.0,
        "www.cv-library.co.uk": 3.0,
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
        if os.getenv("GEMINI_API_KEY"):
            self.gemini_api_key = os.getenv("GEMINI_API_KEY")
        if os.getenv("GEMINI_MODEL"):
            self.gemini_model = os.getenv("GEMINI_MODEL")
        if os.getenv("OLLAMA_BASE_URL"):
            self.ollama_base_url = os.getenv("OLLAMA_BASE_URL")
        if os.getenv("AI_CALL_LIMIT"):
            self.ai_call_limit = int(os.getenv("AI_CALL_LIMIT"))
        if os.getenv("AI_PARSE_LIMIT"):
            self.ai_parse_limit = int(os.getenv("AI_PARSE_LIMIT"))
        if os.getenv("REED_API_KEY"):
            self.reed_api_key = os.getenv("REED_API_KEY")
        if os.getenv("PROXIES_FILE"):
            self.proxies_file = os.getenv("PROXIES_FILE")
        if os.getenv("OUTPUT_DIR"):
            self.output_dir = os.getenv("OUTPUT_DIR")
        if os.getenv("SQLITE_PATH"):
            self.sqlite_path = os.getenv("SQLITE_PATH")
        if os.getenv("PLAYWRIGHT_HEADLESS"):
            self.playwright_headless = os.getenv("PLAYWRIGHT_HEADLESS").lower() != "false"
        if os.getenv("ENRICH_CONTACTS"):
            self.enrich_contacts = os.getenv("ENRICH_CONTACTS").lower() == "true"
