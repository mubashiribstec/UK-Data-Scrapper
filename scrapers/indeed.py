import re
import json
import random
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from scrapers.base import JobRecord
from scrapers.playwright_base import PlaywrightScraper

logger = logging.getLogger(__name__)

INDEED_BASE = "https://uk.indeed.com/jobs"
INDEED_JOB_URL = "https://uk.indeed.com/viewjob?jk={job_id}"
INDEED_LOGIN_URL = "https://secure.indeed.com/auth?hl=en_GB"

MOSAIC_RE = re.compile(
    r'window\.mosaic\.providerData\["mosaic-provider-jobcards"\]\s*=\s*(\{.+?\});',
    re.DOTALL,
)


def _profile_ready(profile_dir: str) -> bool:
    """A persistent Indeed profile exists from a previous --login-indeed run."""
    p = Path(profile_dir)
    return p.is_dir() and any(p.iterdir())


def run_indeed_login(config) -> bool:
    """One-time interactive Indeed login.

    Opens a HEADFUL browser on a persistent profile; the user completes the
    login (email + OTP) manually. Cookies persist to disk and every later
    headless run reuses them automatically.
    """
    scraper = IndeedScraper(config)
    profile_dir = getattr(config, "indeed_profile_dir", "./output/.browser/indeed")

    # Force headful for the login regardless of config
    original_headless = config.playwright_headless
    config.playwright_headless = False
    try:
        scraper._init_playwright(user_data_dir=profile_dir)
        page = scraper._context.new_page()
        page.goto(INDEED_LOGIN_URL, timeout=60000, wait_until="domcontentloaded")

        print("\n" + "=" * 64)
        print("INDEED LOGIN")
        print("=" * 64)
        print("A browser window has opened on the Indeed sign-in page.")
        print("1. Enter your email and continue")
        print("2. When Indeed emails you a one-time code (OTP), enter it")
        print("3. Wait until you are fully logged in (you see your account)")
        print("=" * 64)
        input("When you are logged in, press Enter here to save the session... ")

        page.close()
        logger.info(f"Indeed: login session saved to {profile_dir}")
        print(f"\nSession saved. Future runs will use it automatically (headless).\n")
        return True
    except Exception as e:
        logger.error(f"Indeed login failed: {e}")
        return False
    finally:
        config.playwright_headless = original_headless
        scraper._close_playwright()


class IndeedScraper(PlaywrightScraper):

    def scrape(self, keyword: str, location: str) -> list[JobRecord]:
        profile_dir = getattr(self.config, "indeed_profile_dir", "")
        use_profile = profile_dir and _profile_ready(profile_dir)

        try:
            self._init_playwright(user_data_dir=profile_dir if use_profile else None)
            if use_profile:
                logger.info("Indeed: using saved login session")
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
                    # Appear as a new visitor. With a persistent (logged-in)
                    # profile we only recycle the page — recycling the context
                    # would drop the session.
                    try:
                        page.close()
                        if not self._persistent:
                            ctx.close()
                    except Exception:
                        pass
                    if not self._persistent:
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

                if self._is_blocked(page):
                    logger.warning("Indeed: CAPTCHA/block detected. Saving partial results.")
                    break

                # Primary: structured mosaic JSON embedded in the page.
                # Fallback: CSS selector chains.
                job_cards = self._extract_from_mosaic(page)
                if not job_cards:
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
                                if self._is_blocked(page):
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
                if ctx and not self._persistent:
                    ctx.close()
            except Exception:
                pass
            self._close_playwright()

        logger.info(f"Indeed: scraped {len(results)} jobs for '{keyword}'")
        return results

    # ── Mosaic JSON extraction (primary) ─────────────────────────────────────

    def _extract_from_mosaic(self, page) -> list[JobRecord]:
        """Parse Indeed's embedded mosaic provider JSON — structured data,
        immune to CSS class churn."""
        try:
            html = page.content()
            match = MOSAIC_RE.search(html)
            if not match:
                logger.debug("Indeed: mosaic data not found in page")
                return []
            data = json.loads(match.group(1))
            items = (
                data.get("metaData", {})
                .get("mosaicProviderJobCardsModel", {})
                .get("results", [])
            )
        except Exception as e:
            logger.warning(f"Indeed: mosaic extraction failed ({e}), falling back to selectors")
            return []

        records = []
        for item in items:
            try:
                job_id = item.get("jobkey", "") or ""
                title = item.get("displayTitle") or item.get("title") or "Unknown"
                company = item.get("company") or None
                location = item.get("formattedLocation") or item.get("jobLocationCity") or None

                # Salary: prefer extracted over estimated, keep snippet text
                salary_text = None
                salary_min = salary_max = None
                salary_period = None
                snippet = item.get("salarySnippet") or {}
                if isinstance(snippet, dict) and snippet.get("text"):
                    salary_text = snippet["text"]
                for sal_key in ("extractedSalary", "estimatedSalary"):
                    sal = item.get(sal_key) or {}
                    if isinstance(sal, dict) and (sal.get("min") or sal.get("max")):
                        try:
                            salary_min = float(sal["min"]) if sal.get("min") else None
                            salary_max = float(sal["max"]) if sal.get("max") else None
                        except (TypeError, ValueError):
                            continue
                        sal_type = str(sal.get("type", "")).lower()
                        if "hour" in sal_type:
                            salary_period = "hourly"
                        elif "year" in sal_type or "annual" in sal_type:
                            salary_period = "annual"
                        break

                posted_at = None
                pub = item.get("pubDate")
                if pub:
                    try:
                        posted_at = datetime.utcfromtimestamp(int(pub) / 1000).date().isoformat()
                    except (TypeError, ValueError, OSError):
                        pass

                job_types = item.get("jobTypes") or []
                job_type = ", ".join(str(t).lower() for t in job_types) if job_types else None

                link = item.get("viewJobLink") or ""
                if link.startswith("/"):
                    apply_url = f"https://uk.indeed.com{link}"
                elif job_id:
                    apply_url = INDEED_JOB_URL.format(job_id=job_id)
                else:
                    apply_url = None

                records.append(JobRecord(
                    job_id=job_id,
                    source="indeed",
                    title=title,
                    company=company,
                    location=location,
                    location_city=item.get("jobLocationCity") or None,
                    location_postcode=item.get("jobLocationPostal") or None,
                    salary_text=salary_text,
                    salary_min=salary_min,
                    salary_max=salary_max,
                    salary_period=salary_period,
                    job_type=job_type,
                    posted_at=posted_at,
                    apply_url=apply_url,
                    scraped_at=datetime.utcnow().isoformat() + "Z",
                ))
            except Exception as e:
                logger.debug(f"Indeed: mosaic item parse error: {e}")

        if records:
            logger.debug(f"Indeed: mosaic extraction got {len(records)} jobs")
        return records

    # ── CSS selector extraction (fallback) ───────────────────────────────────

    def _extract_job_cards(self, page) -> list[JobRecord]:
        records = []
        try:
            cards = page.query_selector_all("[data-jk]")
            for card in cards:
                try:
                    job_id = card.get_attribute("data-jk") or ""

                    # Title — try multiple selector patterns used by Indeed across versions
                    title = None
                    for sel in [
                        "h2.jobTitle a span[title]",
                        "h2.jobTitle span[title]",
                        "h2.jobTitle a",
                        "h2.jobTitle",
                        "[data-testid='jobTitle'] a span[title]",
                        "[data-testid='jobTitle'] a",
                        "[data-testid='jobTitle']",
                        "a[id^='job_'] span[title]",
                        "span[title]",
                    ]:
                        el = card.query_selector(sel)
                        if el:
                            # Prefer the title attribute over inner text (cleaner)
                            title = el.get_attribute("title") or el.inner_text().strip()
                            if title:
                                break
                    if not title:
                        title = "Unknown"

                    # Company
                    company = None
                    for sel in [
                        "[data-testid='company-name']",
                        ".companyName",
                        "span.companyName",
                        "[class*='companyName']",
                        "[class*='company-name']",
                        "a[data-tn-element='companyName']",
                    ]:
                        el = card.query_selector(sel)
                        if el:
                            company = el.inner_text().strip() or None
                            if company:
                                break

                    # Location
                    location = None
                    for sel in [
                        "[data-testid='text-location']",
                        ".companyLocation",
                        "[class*='companyLocation']",
                        "[class*='job-location']",
                        "[class*='resultContent'] [class*='location']",
                    ]:
                        el = card.query_selector(sel)
                        if el:
                            location = el.inner_text().strip() or None
                            if location:
                                break

                    # Salary
                    salary_text = None
                    for sel in [
                        "[data-testid='attribute_snippet_testid']",
                        ".salary-snippet-container",
                        "[class*='salary-snippet']",
                        "[class*='salaryText']",
                        ".metadata.salary-snippet-container",
                    ]:
                        el = card.query_selector(sel)
                        if el:
                            salary_text = el.inner_text().strip() or None
                            if salary_text:
                                break

                    # Date posted
                    posted_at = None
                    for sel in [
                        "[data-testid='myJobsStateDate']",
                        "span.date",
                        "[class*='date']",
                    ]:
                        el = card.query_selector(sel)
                        if el:
                            posted_at = el.inner_text().strip() or None
                            if posted_at:
                                break

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
        if self._is_blocked(page):
            return None
        desc_el = page.query_selector("div#jobDescriptionText")
        if desc_el:
            return desc_el.inner_text()[:3000]
        return None
