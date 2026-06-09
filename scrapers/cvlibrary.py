import re
import random
import logging
import time
from datetime import datetime
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from scrapers.base import JobRecord
from scrapers.playwright_base import PlaywrightScraper
from scrapers.jsonld import find_jobpostings, parse_jobposting
from utils.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

CVLIBRARY_SEARCH = "https://www.cv-library.co.uk/search-jobs"


class CVLibraryScraper(PlaywrightScraper):
    def __init__(self, config):
        super().__init__(config)
        self.rate_limiter = RateLimiter(config.domain_delays)

    def scrape(self, keyword: str, location: str) -> list[JobRecord]:
        try:
            self._init_playwright()
        except Exception as e:
            logger.warning(f"CV-Library: Playwright unavailable, skipping. Error: {e}")
            return []

        results = []
        page_no = 1
        ctx = None
        page = None

        geo = ""
        if location and location.lower() not in ("united kingdom", "uk"):
            geo = location

        try:
            ctx = self._new_context()
            page = ctx.new_page()
            self._setup_page(page)

            while len(results) < self.config.max_results_per_keyword:
                self.rate_limiter.wait("www.cv-library.co.uk")
                url = f"{CVLIBRARY_SEARCH}?q={quote_plus(keyword)}"
                if geo:
                    url += f"&geo={quote_plus(geo)}"
                if page_no > 1:
                    url += f"&page={page_no}"

                try:
                    page.goto(url, timeout=30000, wait_until="domcontentloaded")
                    time.sleep(random.uniform(2, 5))
                except Exception as e:
                    logger.warning(f"CV-Library: navigation failed: {e}")
                    break

                if self._is_blocked(page):
                    logger.warning("CV-Library: bot detection triggered. Saving partial results.")
                    break

                page_jobs = self._extract_jobs(page)
                if not page_jobs:
                    logger.debug(f"CV-Library: no jobs found on page {page_no}")
                    break

                for record in page_jobs:
                    if len(results) >= self.config.max_results_per_keyword:
                        break
                    results.append(record)

                page_no += 1
                time.sleep(random.uniform(2, 4))

        except Exception as e:
            logger.error(f"CV-Library scraper error: {e}")
        finally:
            try:
                if page:
                    page.close()
                if ctx:
                    ctx.close()
            except Exception:
                pass
            self._close_playwright()

        logger.info(f"CV-Library: scraped {len(results)} jobs for '{keyword}'")
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
                        records.append(parse_jobposting(data, "cvlibrary"))
                    except Exception as e:
                        logger.debug(f"CV-Library: JSON-LD parse error: {e}")
                if records:
                    return records
        except Exception as e:
            logger.debug(f"CV-Library: JSON-LD extraction failed: {e}")

        # Fallback: search-result card selectors
        return self._extract_cards(page)

    def _extract_cards(self, page) -> list[JobRecord]:
        records = []
        try:
            cards = page.query_selector_all(
                "article[data-job-id], li.results__item article, div.job[data-jobid]"
            )
            for card in cards:
                try:
                    job_id = (
                        card.get_attribute("data-job-id")
                        or card.get_attribute("data-jobid")
                        or ""
                    )
                    title = company = location = salary_text = apply_url = None
                    for sel in ["h2 a", "a.job__title", "[class*='job__title'] a", "[class*='title'] a", "h2"]:
                        el = card.query_selector(sel)
                        if el:
                            title = el.inner_text().strip()
                            href = el.get_attribute("href")
                            if href:
                                apply_url = href if href.startswith("http") else f"https://www.cv-library.co.uk{href}"
                            if title:
                                break
                    for sel in ["[class*='company'] a", "[class*='company']", "p.job__company"]:
                        el = card.query_selector(sel)
                        if el and el.inner_text().strip():
                            company = el.inner_text().strip()
                            break
                    for sel in ["[class*='location']", "dd.job__details-value--location"]:
                        el = card.query_selector(sel)
                        if el and el.inner_text().strip():
                            location = el.inner_text().strip()
                            break
                    el = card.query_selector("[class*='salary']")
                    if el:
                        salary_text = el.inner_text().strip() or None

                    if not title:
                        continue
                    if not job_id and apply_url:
                        m = re.search(r"/job/(\d+)", apply_url)
                        job_id = m.group(1) if m else ""

                    records.append(JobRecord(
                        job_id=job_id or str(abs(hash(f"{title}|{company}|{location}"))),
                        source="cvlibrary",
                        title=title,
                        company=company,
                        location=location,
                        salary_text=salary_text,
                        apply_url=apply_url,
                        scraped_at=datetime.utcnow().isoformat() + "Z",
                    ))
                except Exception as e:
                    logger.debug(f"CV-Library: card parse error: {e}")
        except Exception as e:
            logger.warning(f"CV-Library: card extraction failed: {e}")
        return records
