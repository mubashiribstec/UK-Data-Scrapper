import re
import random
import logging
import time
from datetime import datetime
from typing import Optional

from scrapers.base import BaseScraper, JobRecord
from utils.user_agents import get_random_user_agent

logger = logging.getLogger(__name__)

INDEED_BASE = "https://uk.indeed.com/jobs"
INDEED_JOB_URL = "https://uk.indeed.com/viewjob?jk={job_id}"


def _is_blocked(page) -> bool:
    try:
        title = page.title().lower()
        return any(x in title for x in ["captcha", "robot", "blocked", "verify", "security"])
    except Exception:
        return False


class IndeedScraper(BaseScraper):
    def __init__(self, config):
        super().__init__(config)
        self._playwright = None
        self._browser = None

    def _init_playwright(self):
        try:
            from playwright.sync_api import sync_playwright
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(
                headless=self.config.playwright_headless,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"]
            )
            logger.info("Indeed: Playwright browser launched")
        except Exception as e:
            logger.error(f"Indeed: Failed to launch Playwright: {e}")
            raise

    def _close_playwright(self):
        try:
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass

    def _new_context(self):
        width = random.randint(1280, 1440)
        height = random.randint(700, 800)
        return self._browser.new_context(
            user_agent=get_random_user_agent(),
            viewport={"width": width, "height": height},
            locale="en-GB",
            extra_http_headers={"Accept-Language": "en-GB,en;q=0.9"},
        )

    def scrape(self, keyword: str, location: str) -> list[JobRecord]:
        try:
            self._init_playwright()
        except Exception as e:
            logger.warning(f"Indeed: Playwright unavailable, skipping. Error: {e}")
            return []

        results = []
        offset = 0
        jobs_in_context = 0
        ctx = None
        page = None

        try:
            ctx = self._new_context()
            page = ctx.new_page()
            self._setup_page(page)

            while len(results) < self.config.max_results_per_keyword:
                if jobs_in_context >= 25:
                    # Reset browser context to appear as new visitor
                    try:
                        page.close()
                        ctx.close()
                    except Exception:
                        pass
                    ctx = self._new_context()
                    page = ctx.new_page()
                    self._setup_page(page)
                    jobs_in_context = 0
                    time.sleep(random.uniform(3, 6))

                url = f"{INDEED_BASE}?q={keyword}&l={location}&sort=date&start={offset}"
                try:
                    page.goto(url, timeout=30000, wait_until="domcontentloaded")
                    time.sleep(random.uniform(2, 5))
                except Exception as e:
                    logger.warning(f"Indeed: page navigation failed: {e}")
                    break

                if _is_blocked(page):
                    logger.warning("Indeed: CAPTCHA/block detected. Saving partial results.")
                    break

                # Extract job cards
                job_cards = self._extract_job_cards(page)
                if not job_cards:
                    logger.debug(f"Indeed: no job cards found at offset {offset}")
                    break

                for card_data in job_cards:
                    if len(results) >= self.config.max_results_per_keyword:
                        break
                    results.append(card_data)
                    jobs_in_context += 1

                # Fetch descriptions for top 20 only
                if offset == 0:
                    for i, record in enumerate(results[:20]):
                        if not record.description and record.job_id:
                            try:
                                desc = self._fetch_description(page, record.job_id)
                                record.description = desc
                                jobs_in_context += 1
                                if _is_blocked(page):
                                    logger.warning("Indeed: blocked during description fetch")
                                    break
                            except Exception as e:
                                logger.debug(f"Indeed: description fetch failed for {record.job_id}: {e}")

                offset += 10
                time.sleep(random.uniform(2, 5))

        except Exception as e:
            logger.error(f"Indeed scraper error: {e}")
        finally:
            try:
                if page:
                    page.close()
                if ctx:
                    ctx.close()
            except Exception:
                pass
            self._close_playwright()

        logger.info(f"Indeed: scraped {len(results)} jobs for '{keyword}'")
        return results

    def _setup_page(self, page):
        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-GB', 'en']});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3]});
        """)
        # Block resources to reduce fingerprint and speed up
        page.route("**/*.{png,jpg,jpeg,gif,svg,ico,woff,woff2,ttf,eot}", lambda r: r.abort())
        page.route("**/*.css", lambda r: r.abort())

    def _extract_job_cards(self, page) -> list[JobRecord]:
        records = []
        try:
            cards = page.query_selector_all("[data-jk]")
            for card in cards:
                try:
                    job_id = card.get_attribute("data-jk") or ""
                    title_el = card.query_selector("[data-testid='jobTitle'] a, h2[data-testid='jobTitle']")
                    title = title_el.inner_text().strip() if title_el else "Unknown"
                    company_el = card.query_selector("[data-testid='company-name']")
                    company = company_el.inner_text().strip() if company_el else None
                    location_el = card.query_selector("[data-testid='text-location']")
                    location = location_el.inner_text().strip() if location_el else None
                    salary_el = card.query_selector("[data-testid='attribute_snippet_testid']")
                    salary_text = salary_el.inner_text().strip() if salary_el else None
                    date_el = card.query_selector("[data-testid='myJobsStateDate'], span.date")
                    posted_at = date_el.inner_text().strip() if date_el else None

                    apply_url = INDEED_JOB_URL.format(job_id=job_id) if job_id else None

                    records.append(JobRecord(
                        job_id=job_id,
                        source="indeed",
                        title=title,
                        company=company,
                        location=location,
                        salary_text=salary_text,
                        posted_at=posted_at,
                        apply_url=apply_url,
                        scraped_at=datetime.utcnow().isoformat() + "Z",
                    ))
                except Exception as e:
                    logger.debug(f"Indeed: card parse error: {e}")
        except Exception as e:
            logger.warning(f"Indeed: failed to extract job cards: {e}")
        return records

    def _fetch_description(self, page, job_id: str) -> Optional[str]:
        url = INDEED_JOB_URL.format(job_id=job_id)
        page.goto(url, timeout=20000, wait_until="domcontentloaded")
        time.sleep(random.uniform(1, 3))
        if _is_blocked(page):
            return None
        desc_el = page.query_selector("div#jobDescriptionText")
        if desc_el:
            return desc_el.inner_text()[:3000]
        return None
