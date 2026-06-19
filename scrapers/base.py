from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class JobRecord:
    job_id: str
    source: str                    # "reed" | "indeed"
    title: str
    company: Optional[str] = None
    company_url: Optional[str] = None
    location: Optional[str] = None
    location_city: Optional[str] = None
    location_postcode: Optional[str] = None
    salary_text: Optional[str] = None
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    salary_period: Optional[str] = None   # "hourly" | "annual"
    job_type: Optional[str] = None        # "full-time" | "part-time" | "contract"
    description: Optional[str] = None
    requirements: list = field(default_factory=list)
    benefits: list = field(default_factory=list)
    posted_at: Optional[str] = None
    expires_at: Optional[str] = None
    apply_url: Optional[str] = None
    sources: list = field(default_factory=list)   # populated by dedup: may list multiple origins
    field_sources: dict = field(default_factory=dict)  # populated by dedup: field name -> origin source
    _hash: Optional[str] = None
    scraped_at: Optional[str] = None      # ISO timestamp

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "source": self.source,
            "sources": self.sources or [self.source],
            "title": self.title,
            "company": self.company,
            "company_url": self.company_url,
            "location": self.location,
            "location_city": self.location_city,
            "location_postcode": self.location_postcode,
            "salary_text": self.salary_text,
            "salary_min": self.salary_min,
            "salary_max": self.salary_max,
            "salary_period": self.salary_period,
            "job_type": self.job_type,
            "description": self.description,
            "requirements": self.requirements,
            "benefits": self.benefits,
            "posted_at": self.posted_at,
            "expires_at": self.expires_at,
            "apply_url": self.apply_url,
            "_hash": self._hash,
            "scraped_at": self.scraped_at,
            "field_sources": self.field_sources,
        }


class BaseScraper(ABC):
    def __init__(self, config):
        self.config = config

    @abstractmethod
    def scrape(self, keyword: str, location: str) -> list:
        """Scrape jobs for a given keyword/location combo."""
        pass

    def scrape_all(self) -> list:
        """Run scrape() for every keyword × location combination."""
        results = []
        for kw in self.config.keywords:
            for loc in self.config.locations:
                try:
                    batch = self.scrape(kw, loc)
                    results.extend(batch)
                    logger.info(f"{self.__class__.__name__} got {len(batch)} jobs for '{kw}' in '{loc}'")
                except Exception as e:
                    logger.error(f"{self.__class__.__name__} failed for '{kw}' in '{loc}': {e}")
        return results
