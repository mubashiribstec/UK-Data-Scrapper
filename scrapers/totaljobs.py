import re
import random
import logging
import time
from datetime import datetime

from bs4 import BeautifulSoup

from scrapers.base import JobRecord
from scrapers.playwright_base import PlaywrightScraper
from scrapers.jsonld import find_jobpostings, parse_jobposting
from utils.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

TOTALJOBS_BASE = "https://www.totaljobs.com/jobs"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


class TotalJobsScraper(PlaywrightScraper):
    def __init__(self, config):
        super().__init__(config)
        self.rate_limiter = RateLimiter(config.domain_delays)

    def scrape(self, keyword: str, location: str) -> list[JobRecord]:
        try:
            self._init_playwright()
        except Exception as e:
            logger.warning(f"TotalJobs: Playwright unavailable, skipping. Error: {e}")
            return []

        results = []
        page_no = 1
        ctx = None
        page = None

        kw_slug = _slug(keyword)
        loc_slug = None
        if location and location.lower() not in ("united kingdom", "uk"):
            loc_slug = _slug(location)

        try:
            ctx = self._new_context()
            page = ctx.new_page()
            self._setup_page(page)

            while len(results) < self.config.max_results_per_keyword:
                self.rate_limiter.wait("www.totaljobs.com")
                url = f"{TOTALJOBS_BASE}/{kw_slug}"
                if loc_slug:
                    url += f"/in-{loc_slug}"
                if page_no > 1:
                    url += f"?page={page_no}"

                try:
                    page.goto(url, timeout=30000, wait_until="domcontentloaded")
                    time.sleep(random.uniform(2, 5))
                except Exception as e:
                    logger.warning(f"TotalJobs: navigation failed: {e}")
                    break

                if self._is_blocked(page):
                    logger.warning("TotalJobs: bot detection triggered. Saving partial results.")
                    break

                page_jobs = self._extract_jobs(page)
                if not page_jobs:
                    logger.debug(f"TotalJobs: no jobs found on page {page_no}")
                    break

                for record in page_jobs:
                    if len(results) >= self.config.max_results_per_keyword:
                        break
                    results.append(record)

                page_no += 1
                time.sleep(random.uniform(2, 4))

        except Exception as e:
            logger.error(f"TotalJobs scraper error: {e}")
        finally:
            try:
                if page:
                    page.close()
                if ctx:
                    ctx.close()
            except Exception:
                pass
            self._close_playwright()

        logger.info(f"TotalJobs: scraped {len(results)} jobs for '{keyword}'")
        return results

    def _extract_jobs(self, page) -> list[JobRecord]:
        # Primary: schema.org JSON-LD embedded for SEO
        try:
            soup = BeautifulSoup(page.content(), "lxml")
            postings = find_jobpostings(soup)
            if postings:
                records = []
                for data in postings:
                    try:
                        records.append(parse_jobposting(data, "totaljobs"))
                    except Exception as e:
                        logger.debug(f"TotalJobs: JSON-LD parse error: {e}")
                if records:
                    return records
        except Exception as e:
            logger.debug(f"TotalJobs: JSON-LD extraction failed: {e}")

        # Fallback: StepStone design-system card selectors
        return self._extract_cards(page)

    def _extract_cards(self, page) -> list[JobRecord]:
        records = []
        try:
            cards = page.query_selector_all(
                "article[data-at='job-item'], div[data-at='job-item'], article[data-genesis-element='CARD']"
            )
            for card in cards:
                try:
                    title = company = location = salary_text = apply_url = None
                    for sel in ["[data-at='job-item-title']", "h2 a", "a[data-at='job-item-title']", "h2"]:
                        el = card.query_selector(sel)
                        if el:
                            title = el.inner_text().strip()
                            href = el.get_attribute("href")
                            if href:
                                apply_url = href if href.startswith("http") else f"https://www.totaljobs.com{href}"
                            if title:
                                break
                    for sel in ["[data-at='job-item-company-name']", "[class*='company']"]:
                        el = card.query_selector(sel)
                        if el and el.inner_text().strip():
                            company = el.inner_text().strip()
                            break
                    for sel in ["[data-at='job-item-location']", "[class*='location']"]:
                        el = card.query_selector(sel)
                        if el and el.inner_text().strip():
                            location = el.inner_text().strip()
                            break
                    el = card.query_selector("[data-at='job-item-salary-info'], [class*='salary']")
                    if el:
                        salary_text = el.inner_text().strip() or None

                    if not title:
                        continue
                    job_id = ""
                    if apply_url:
                        m = re.search(r"job(\d+)", apply_url)
                        job_id = m.group(1) if m else apply_url.rstrip("/").split("/")[-1]

                    records.append(JobRecord(
                        job_id=job_id or str(abs(hash(f"{title}|{company}|{location}"))),
                        source="totaljobs",
                        title=title,
                        company=company,
                        location=location,
                        salary_text=salary_text,
                        apply_url=apply_url,
                        scraped_at=datetime.utcnow().isoformat() + "Z",
                    ))
                except Exception as e:
                    logger.debug(f"TotalJobs: card parse error: {e}")
        except Exception as e:
            logger.warning(f"TotalJobs: card extraction failed: {e}")
        return records
